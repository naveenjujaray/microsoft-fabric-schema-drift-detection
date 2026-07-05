"""Fabric Schema Drift Detective - CLI entry point.

Usage:
    python main.py --mode simulate --once            # one detection run
    python main.py --mode simulate --baseline        # (re)capture baselines
    python main.py --mode simulate --once --dry-run  # render notifications only
    python main.py --mode live --once                # against real Fabric
    python main.py --provision                       # print fab provisioning steps
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from rich.console import Console

# Windows consoles often default to cp1252; drift output uses unicode.
if hasattr(sys.stdout, "reconfigure"):  # pragma: no cover
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from src.backends.base import Layer, SchemaBackend
from src.config import load_config
from src.git_handler import GitHandler
from src.lineage import annotate_downstream
from src.llm_reasoner import make_reasoner
from src.medallion import build_lineage_graph
from src.notifications import DriftAlert, build_dispatcher
from src.schema_diff import DriftRecord, DriftType, diff_all
from src.schema_store import BaselineError, SchemaStore
from src.workspace import load_registry

console = Console()
logger = logging.getLogger("drift-detective")

# process exit codes (documented in README)
EXIT_CLEAN = 0
EXIT_CRITICAL_DRIFT = 1
EXIT_CONFIG_ERROR = 2
EXIT_BASELINE_ERROR = 3


def make_backend(mode: str, cfg: dict[str, Any]) -> SchemaBackend:
    """Backend factory: identical downstream code path either way."""
    if mode == "live":
        import os

        from src.backends.fabric_backend import FabricBackend

        # propagate config auth choice (env var still wins if already set)
        auth_method = cfg.get("fabric", {}).get("auth_method", "")
        if auth_method:
            os.environ.setdefault("FABRIC_AUTH_METHOD", auth_method)

        return FabricBackend(
            cfg.get("fabric", {}),
            reports_dir=cfg.get("git", {}).get("reports_dir", "pbip_reports"),
        )
    from src.backends.local_backend import LocalBackend

    sim = cfg.get("simulate", {})
    return LocalBackend(
        db_path=sim.get("db_path", "sample_data/warehouse.duckdb"),
        semantic_model_path=sim.get(
            "semantic_model_path", "sample_data/generated/semantic_model.json"
        ),
        reports_path=sim.get("reports_path", "sample_data/generated/reports.json"),
    )


def capture_baseline(backend: SchemaBackend, store: SchemaStore) -> None:
    schemas = backend.get_all_schemas()
    store.save_all(schemas)
    console.print(
        f"[green]Baseline captured for {len(schemas)} layer(s) "
        f"into {store.directory}/[/]"
    )


def _tmdl_excerpt(cfg: dict[str, Any]) -> str:
    """Best-effort semantic model excerpt for the fix prompt."""
    sim_path = Path(
        cfg.get("simulate", {}).get(
            "semantic_model_path", "sample_data/generated/semantic_model.json"
        )
    )
    if sim_path.exists():
        return sim_path.read_text(encoding="utf-8")[:8000]
    return "(semantic model definition unavailable)"


def run_once(
    mode: str,
    cfg: dict[str, Any],
    dry_run: bool = False,
    open_pr: bool = False,
) -> int:
    """One full detection cycle. Returns count of critical drifts."""
    backend = make_backend(mode, cfg)
    store = SchemaStore(cfg.get("baseline", {}).get("dir", ".baselines"))

    # Baselines must exist and be complete. A missing baseline is NEVER
    # silently recreated: recapturing would swallow whatever drifted since
    # the file vanished. The operator must run --baseline explicitly.
    if not store.has_baselines():
        raise BaselineError(
            f"no baselines found in {store.directory}/ - capture them "
            "explicitly with:  python main.py --baseline"
        )

    current = backend.get_all_schemas()
    missing = store.missing_layers(current.keys())
    if missing:
        names = ", ".join(layer.value for layer in missing)
        raise BaselineError(
            f"baseline file(s) missing for layer(s): {names}. "
            "Baselines are never recreated implicitly - if this is "
            "intentional, re-capture with:  python main.py --baseline"
        )
    baselines = store.load_all()

    graph = build_lineage_graph(
        baselines.get(Layer.SEMANTIC_MODEL), baselines.get(Layer.REPORTS)
    )
    workspaces = load_registry(
        cfg.get("lineage", {}).get("workspaces_manifest", "")
    )
    drifts: list[DriftRecord] = diff_all(baselines, current)
    drifts = annotate_downstream(drifts, graph, workspaces)

    if not drifts:
        console.print("[green]No schema drift detected.[/]")
        return 0

    ws_breaks = [
        d for d in drifts
        if d.drift_type is DriftType.CROSS_WORKSPACE_BREAK
    ]
    if ws_breaks:
        impacted_ws = sorted({d.workspace for d in ws_breaks if d.workspace})
        console.print(
            f"[bold red]Cross-workspace impact:[/] {len(ws_breaks)} break(s) "
            f"reaching workspace(s): {', '.join(impacted_ws)}"
        )

    # --- Claude reasoning -------------------------------------------------
    reasoner = make_reasoner(cfg.get("llm", {}), workspaces)
    impact = reasoner.analyze_impact(drifts)
    summary = impact.get("summary", "")
    for analysis in impact.get("analyses", []):
        idx = analysis.get("drift_index")
        sev = analysis.get("severity")
        if idx is not None and 0 <= idx < len(drifts) and sev:
            # let Claude escalate/downgrade severity, defensively
            from src.schema_diff import Severity

            try:
                drifts[idx].severity = Severity(sev)
            except ValueError:
                pass

    fixes = reasoner.suggest_fixes(drifts, _tmdl_excerpt(cfg)).get("fixes", [])
    pr_content = reasoner.write_pr_content(drifts, summary)

    # --- Git PR -----------------------------------------------------------
    git_cfg = cfg.get("git", {})
    handler = GitHandler(
        reports_dir=git_cfg.get("reports_dir", "pbip_reports"),
        remote=git_cfg.get("remote", "origin"),
        base_branch=git_cfg.get("base_branch", "main"),
        branch_prefix=git_cfg.get("branch_prefix", "drift-fix/"),
        use_gh_cli=git_cfg.get("use_gh_cli", True),
    )
    outcome = handler.create_pr(fixes, pr_content, dry_run=dry_run or not open_pr)
    if outcome.dry_run:
        console.rule("[bold]PR that would be opened")
        console.print(f"[bold]branch:[/] {outcome.branch}")
        console.print(f"[bold]commit:[/] {outcome.commit_subject}")
        console.print(f"[bold]title :[/] {outcome.pr_title}")
        from rich.markup import escape as _esc

        console.print(_esc(outcome.pr_body))
        console.rule()

    # --- Notifications ----------------------------------------------------
    alert = DriftAlert(
        drifts=drifts,
        summary=summary,
        pr_url=outcome.pr_url,
        pr_title=outcome.pr_title,
        environment=mode,
    )
    dispatcher = build_dispatcher(cfg.get("notifications", {}))
    results = dispatcher.dispatch(alert, dry_run=dry_run)
    console.print(f"[bold]Notification results:[/] {results}")

    return sum(1 for d in drifts if d.severity.value == "critical")


def run_agent_cli(
    name: str,
    task: str | None,
    mode: str,
    cfg: dict[str, Any],
    allow_writes: bool,
    max_turns: int | None,
) -> int:
    """Run one named agent and print its report."""
    from rich.markup import escape

    from src.agents import AGENT_SPECS, ToolContext, run_agent

    spec = AGENT_SPECS.get(name)
    if spec is None:
        console.print(f"[red]Unknown agent:[/] {name}")
        console.print("Available: " + ", ".join(sorted(AGENT_SPECS)))
        return 2

    task = task or spec.default_task
    if not task:
        console.print(
            f"[red]Agent '{name}' needs --task[/] (no default task defined)."
        )
        return 2
    if spec.needs_writes and not allow_writes:
        console.print(
            f"[yellow]Note:[/] '{name}' normally edits files/items; without "
            "--allow-writes it will only produce a plan."
        )

    context = ToolContext.build(mode, cfg, allow_writes=allow_writes)
    llm_cfg = dict(cfg.get("llm", {}))
    result = run_agent(name, task, context, llm_cfg, max_turns=max_turns)

    console.rule(f"[bold]agent: {name}")
    console.print(escape(result.output))
    console.rule()
    console.print(
        f"[bold]run:[/] success={result.success} turns={result.turns} "
        f"tools={len(result.tool_calls)} "
        f"tokens={result.input_tokens}+{result.output_tokens} "
        f"stop={result.stop_reason}"
    )
    if result.log_path:
        console.print(f"[bold]log:[/] {result.log_path}")
    return 0 if result.success else 1


def list_agents_cli() -> int:
    from rich.table import Table

    from src.agents import AGENT_SPECS

    table = Table(title="Available agents (python main.py --agent <name>)")
    table.add_column("agent", style="bold")
    table.add_column("what it does")
    table.add_column("writes?", justify="center")
    for name, spec in sorted(AGENT_SPECS.items()):
        table.add_row(name, spec.description, "yes" if spec.needs_writes else "-")
    console.print(table)
    console.print(
        "Write agents only modify anything when run with [bold]--allow-writes[/]."
    )
    return 0


def print_provisioning() -> None:
    script = Path("scripts/provision_fabric.sh")
    console.print(
        "Provisioning uses the Fabric CLI (`pip install ms-fabric-cli`).\n"
        f"See [bold]{script}[/] and docs/FABRIC_SETUP.md for the full, "
        "verified `fab` command sequence."
    )
    if script.exists():
        console.print(script.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fabric Schema Drift Detective")
    parser.add_argument("--mode", choices=["live", "simulate"], default=None,
                        help="override config.yaml mode")
    parser.add_argument("--once", action="store_true", help="run one cycle")
    parser.add_argument("--baseline", action="store_true",
                        help="capture/refresh baseline snapshots")
    parser.add_argument("--dry-run", action="store_true",
                        help="render notifications/PR without sending")
    parser.add_argument("--open-pr", action="store_true",
                        help="actually branch/commit/push and open the PR")
    parser.add_argument("--provision", action="store_true",
                        help="show Fabric provisioning steps")
    parser.add_argument("--agent", metavar="NAME",
                        help="run a tool-use agent (see --list-agents)")
    parser.add_argument("--task", metavar="TEXT",
                        help="task for --agent (falls back to the agent's default)")
    parser.add_argument("--list-agents", action="store_true",
                        help="list available agents and exit")
    parser.add_argument("--allow-writes", action="store_true",
                        help="let the agent use write tools (TMDL edits, "
                             "fab create, git push)")
    parser.add_argument("--max-turns", type=int, default=None,
                        help="override the agent's turn cap")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.provision:
        print_provisioning()
        return 0

    if args.list_agents:
        return list_agents_cli()

    cfg = load_config(args.config)
    mode = args.mode or cfg.get("mode", "simulate")

    if args.agent:
        try:
            return run_agent_cli(
                args.agent, args.task, mode, cfg,
                allow_writes=args.allow_writes, max_turns=args.max_turns,
            )
        except (OSError, ValueError) as exc:
            console.print(f"[red]Configuration error:[/] {exc}")
            return EXIT_CONFIG_ERROR

    if args.baseline:
        capture_baseline(make_backend(mode, cfg), SchemaStore(
            cfg.get("baseline", {}).get("dir", ".baselines")))
        return 0

    if args.once:
        try:
            criticals = run_once(
                mode, cfg, dry_run=args.dry_run, open_pr=args.open_pr
            )
        except BaselineError as exc:
            console.print(f"[red]Baseline error:[/] {exc}")
            return EXIT_BASELINE_ERROR
        except (OSError, ValueError) as exc:
            console.print(f"[red]Configuration error:[/] {exc}")
            return EXIT_CONFIG_ERROR
        return EXIT_CRITICAL_DRIFT if criticals else EXIT_CLEAN

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
