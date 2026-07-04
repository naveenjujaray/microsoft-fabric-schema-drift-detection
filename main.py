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
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.backends.base import Layer, SchemaBackend
from src.config import load_config
from src.git_handler import GitHandler
from src.lineage import annotate_downstream
from src.llm_reasoner import make_reasoner
from src.medallion import build_lineage_graph
from src.notifications import DriftAlert, build_dispatcher
from src.schema_diff import DriftRecord, diff_all
from src.schema_store import SchemaStore

console = Console()
logger = logging.getLogger("drift-detective")


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

    if not store.has_baselines():
        console.print("[yellow]No baselines found - capturing initial snapshot; "
                      "run again after schema changes.[/]")
        capture_baseline(backend, store)
        return 0

    current = backend.get_all_schemas()
    baselines = store.load_all()

    graph = build_lineage_graph(
        baselines.get(Layer.SEMANTIC_MODEL), baselines.get(Layer.REPORTS)
    )
    drifts: list[DriftRecord] = diff_all(baselines, current)
    drifts = annotate_downstream(drifts, graph)

    if not drifts:
        console.print("[green]No schema drift detected.[/]")
        return 0

    # --- Claude reasoning -------------------------------------------------
    reasoner = make_reasoner(cfg.get("llm", {}))
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

    cfg = load_config(args.config)
    mode = args.mode or cfg.get("mode", "simulate")

    if args.baseline:
        capture_baseline(make_backend(mode, cfg), SchemaStore(
            cfg.get("baseline", {}).get("dir", ".baselines")))
        return 0

    if args.once:
        try:
            criticals = run_once(
                mode, cfg, dry_run=args.dry_run, open_pr=args.open_pr
            )
        except (ValueError, EnvironmentError) as exc:
            console.print(f"[red]Configuration error:[/] {exc}")
            return 2
        return 1 if criticals else 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
