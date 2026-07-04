"""Guard-railed tool registry for the agent runtime.

Every tool is a thin, defensive wrapper over an existing module
(schema_diff, lineage, backends, git). Guard rails:

* **Write gating** — tools marked ``writes=True`` refuse to run unless
  the ToolContext was built with ``allow_writes=True`` (CLI flag
  ``--allow-writes``). Read tools can never mutate anything.
* **Path sandbox** — file tools resolve paths against the repo root and
  refuse traversal outside it, plus deny-list (.env, .git).
* **SQL sandbox** — ``run_sql`` accepts a single SELECT/WITH statement,
  blocks DDL/DML keywords, opens DuckDB read-only, caps rows.
* **Bounded output** — every tool truncates its result so one call
  can't blow the context window.
* **No raised exceptions** — tool errors return an ``ERROR: ...``
  string to the model (agents recover; the loop never crashes).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..backends.base import Layer, SchemaBackend
from ..lineage import LineageGraph
from ..schema_diff import diff_all
from ..schema_store import SchemaStore
from ..workspace import WorkspaceRegistry, load_registry

_MAX_RESULT_CHARS = 8000
_SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|copy|pragma|install|load|"
    r"export|import|call|set|grant|vacuum)\b",
    re.IGNORECASE,
)
_FAB_READ_COMMANDS = {"ls", "get", "exists", "auth"}
_FAB_WRITE_COMMANDS = {"create", "mkdir", "api", "deploy"}


def _clip(text: str, limit: int = _MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def _json(obj: Any) -> str:
    return _clip(json.dumps(obj, indent=2, default=str))


# ---------------------------------------------------------------------------
@dataclass
class ToolContext:
    """Everything tools need, bound once per agent run."""

    cfg: dict[str, Any]
    mode: str
    backend: SchemaBackend
    store: SchemaStore
    graph: LineageGraph
    repo_dir: Path
    reports_dir: Path
    allow_writes: bool = False
    workspaces: WorkspaceRegistry | None = None

    @property
    def db_path(self) -> Path | None:
        if self.mode != "simulate":
            return None
        return self.repo_dir / self.cfg.get("simulate", {}).get(
            "db_path", "sample_data/warehouse.duckdb"
        )

    @classmethod
    def build(
        cls, mode: str, cfg: dict[str, Any], allow_writes: bool = False
    ) -> "ToolContext":
        """Assemble backend + baselines + lineage graph for agent use."""
        # local import avoids a circular import with main.py
        from ..medallion import build_lineage_graph

        if mode == "live":
            from ..backends.fabric_backend import FabricBackend

            backend: SchemaBackend = FabricBackend(
                cfg.get("fabric", {}),
                reports_dir=cfg.get("git", {}).get("reports_dir", "pbip_reports"),
            )
        else:
            from ..backends.local_backend import LocalBackend

            sim = cfg.get("simulate", {})
            backend = LocalBackend(
                db_path=sim.get("db_path", "sample_data/warehouse.duckdb"),
                semantic_model_path=sim.get(
                    "semantic_model_path",
                    "sample_data/generated/semantic_model.json",
                ),
                reports_path=sim.get(
                    "reports_path", "sample_data/generated/reports.json"
                ),
            )
        store = SchemaStore(cfg.get("baseline", {}).get("dir", ".baselines"))
        baselines = store.load_all()
        graph = build_lineage_graph(
            baselines.get(Layer.SEMANTIC_MODEL), baselines.get(Layer.REPORTS)
        )
        workspaces = load_registry(
            cfg.get("lineage", {}).get("workspaces_manifest", "")
        )
        repo_dir = Path.cwd()
        return cls(
            cfg=cfg,
            mode=mode,
            backend=backend,
            store=store,
            graph=graph,
            repo_dir=repo_dir,
            reports_dir=repo_dir
            / cfg.get("git", {}).get("reports_dir", "pbip_reports"),
            allow_writes=allow_writes,
            workspaces=workspaces,
        )

    # ------------------------------------------------------------------
    def safe_path(self, rel: str, root: Path | None = None) -> Path:
        """Resolve ``rel`` inside ``root`` (default repo); raise on escape.

        Also rejects absolute paths and any symlink component - a
        symlink inside the sandbox could otherwise redirect writes to
        an arbitrary location.
        """
        if Path(rel).is_absolute():
            raise PermissionError(f"absolute paths not allowed: {rel}")
        root = (root or self.repo_dir).resolve()
        path = (root / rel).resolve()
        if not path.is_relative_to(root):
            raise PermissionError(f"path escapes sandbox: {rel}")
        parts = {p.lower() for p in path.parts}
        if ".env" in parts or ".git" in parts:
            raise PermissionError(f"access to {rel} denied")
        probe = root
        for part in path.relative_to(root).parts:
            probe = probe / part
            if probe.is_symlink():
                raise PermissionError(f"symlink in path not allowed: {rel}")
        return path


# ---------------------------------------------------------------------------
@dataclass
class Tool:
    """One callable tool exposed to Claude."""

    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., str]
    writes: bool = False


class ToolRegistry:
    """Holds tools; renders Anthropic definitions; dispatches calls."""

    def __init__(self, context: ToolContext) -> None:
        self.context = context
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools)

    def definitions(self) -> list[dict[str, Any]]:
        """Anthropic ``tools=`` parameter payload."""
        return [
            {
                "name": t.name,
                "description": t.description
                + (" [WRITE TOOL - requires --allow-writes]" if t.writes else ""),
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    def dispatch(self, name: str, tool_input: dict[str, Any]) -> str:
        """Execute one tool call; never raises."""
        tool = self._tools.get(name)
        if tool is None:
            return f"ERROR: unknown tool {name!r}"
        if tool.writes and not self.context.allow_writes:
            return (
                f"ERROR: {name} is a write tool and writes are disabled. "
                "Explain what you would change instead; the operator can "
                "re-run with --allow-writes."
            )
        try:
            return _clip(str(tool.fn(**tool_input)))
        except TypeError as exc:  # bad/missing arguments from the model
            return f"ERROR: bad arguments for {name}: {exc}"
        except Exception as exc:  # noqa: BLE001 - surfaced to the model
            return f"ERROR: {name} failed: {exc}"


# ---------------------------------------------------------------------------
# tool implementations
# ---------------------------------------------------------------------------
def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
    }


def build_registry(
    context: ToolContext, allowed: tuple[str, ...] | None = None
) -> ToolRegistry:
    """All tools, optionally filtered to an agent's whitelist."""
    registry = ToolRegistry(context)
    ctx = context

    # ---------------- schema / drift ----------------------------------
    def get_schema(layer: str) -> str:
        schema = ctx.backend.get_schema(Layer(layer))
        return _json(schema.to_dict())

    registry.register(Tool(
        name="get_schema",
        description=(
            "Current schema of one medallion layer "
            "(bronze|silver|gold|semantic_model|reports): tables, columns, "
            "types, nullability, keys, measures."
        ),
        input_schema=_obj({"layer": {"type": "string"}}, ["layer"]),
        fn=get_schema,
    ))

    def run_diff() -> str:
        from ..lineage import annotate_downstream

        baselines = ctx.store.load_all()
        if not baselines:
            return "ERROR: no baselines captured yet (run --baseline first)"
        current = ctx.backend.get_all_schemas()
        drifts = annotate_downstream(
            diff_all(baselines, current), ctx.graph, ctx.workspaces
        )
        return _json([d.to_dict() for d in drifts])

    registry.register(Tool(
        name="run_diff",
        description=(
            "Run full drift detection now: baseline vs current schemas across "
            "all layers, including lineage-derived cross_layer_break records. "
            "Returns the drift list as JSON."
        ),
        input_schema=_obj({}),
        fn=run_diff,
    ))

    # ---------------- lineage ----------------------------------------
    def query_lineage(node: str, direction: str = "downstream") -> str:
        if direction not in ("downstream", "upstream"):
            return "ERROR: direction must be downstream or upstream"
        result = (
            ctx.graph.downstream(node)
            if direction == "downstream"
            else ctx.graph.upstream(node)
        )
        return _json({"node": node, "direction": direction, "impacted": result})

    registry.register(Tool(
        name="query_lineage",
        description=(
            "Walk the lineage graph from a node id "
            "('layer:table.column' or 'layer:Table#Measure'). "
            "direction=downstream lists everything the node feeds; "
            "upstream lists everything it depends on."
        ),
        input_schema=_obj(
            {"node": {"type": "string"}, "direction": {"type": "string"}},
            ["node"],
        ),
        fn=query_lineage,
    ))

    def list_lineage_nodes(prefix: str = "") -> str:
        nodes: set[str] = set()
        for src, dst in ctx.graph.edges():
            nodes.add(src)
            nodes.add(dst)
        matches = sorted(n for n in nodes if prefix.lower() in n.lower())
        return _json(matches[:300])

    registry.register(Tool(
        name="list_lineage_nodes",
        description=(
            "List lineage node ids, optionally filtered by a substring "
            "(e.g. 'freight' or 'silver:'). Use to find exact node ids "
            "before calling query_lineage."
        ),
        input_schema=_obj({"prefix": {"type": "string"}}),
        fn=list_lineage_nodes,
    ))

    def workspace_map(node: str = "") -> str:
        if ctx.workspaces is None:
            return (
                "ERROR: no workspace manifest configured "
                "(set lineage.workspaces_manifest in config.yaml)"
            )
        reg = ctx.workspaces
        out: dict[str, Any] = {
            "tenant_id": reg.tenant_id,
            "workspaces": [
                {
                    "name": ws.name,
                    "workspace_id": ws.workspace_id,
                    "items": [
                        {"name": i.name, "type": i.item_type,
                         "layers": [layer.value for layer in i.layers]}
                        for i in ws.items
                    ],
                }
                for ws in reg.workspaces
            ],
            "links": [
                {"type": ln.link_type,
                 "from": f"{ln.src_workspace}:{ln.src_layer.value}",
                 "to": f"{ln.dst_workspace}:{ln.dst_layer.value}"}
                for ln in reg.links
            ],
        }
        if node:
            down = ctx.graph.downstream(node)
            out["node"] = node
            out["workspace_path"] = reg.workspace_path(node)
            out["cross_workspace_blast_radius"] = reg.blast_radius(down)
        return _json(out)

    registry.register(Tool(
        name="workspace_map",
        description=(
            "Cross-workspace topology: workspaces, their Fabric items, and "
            "the shortcut/mirror/semantic-model links between them. Pass an "
            "optional lineage node id to also get its workspace path and "
            "per-workspace blast radius."
        ),
        input_schema=_obj({"node": {"type": "string"}}),
        fn=workspace_map,
    ))

    def count_downstream_reports(node: str) -> str:
        down = ctx.graph.downstream(node)
        reports = sorted({
            n.split(":", 1)[1].split(".", 1)[0]
            for n in down if n.startswith("reports:")
        })
        measures = [n for n in down if "#" in n]
        return _json({
            "node": node,
            "total_downstream": len(down),
            "affected_reports": reports,
            "affected_measures": measures,
        })

    registry.register(Tool(
        name="count_downstream_reports",
        description=(
            "Blast radius of one lineage node: how many downstream assets, "
            "which Power BI reports, which DAX measures."
        ),
        input_schema=_obj({"node": {"type": "string"}}, ["node"]),
        fn=count_downstream_reports,
    ))

    # ---------------- data profiling (simulate/DuckDB only) ----------
    def _duckdb():
        if ctx.db_path is None or not ctx.db_path.exists():
            raise RuntimeError(
                "data profiling is only available in simulate mode "
                "(live mode: use get_schema / run_diff instead)"
            )
        import duckdb

        return duckdb.connect(str(ctx.db_path), read_only=True)

    _IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_ ]*$")

    def _qident(name: str) -> str:
        if not _IDENT.match(name):
            raise ValueError(f"invalid identifier: {name!r}")
        return '"' + name.replace('"', "") + '"'

    def profile_column(layer: str, table: str, column: str) -> str:
        con = _duckdb()
        try:
            rel = f"{_qident(layer)}.{_qident(table)}"
            col = _qident(column)
            row = con.execute(
                f"SELECT count(*), count(DISTINCT {col}), "
                f"count(*) - count({col}), min({col}), max({col}) FROM {rel}"
            ).fetchone()
        finally:
            con.close()
        return _json({
            "column": f"{layer}.{table}.{column}",
            "rows": row[0], "distinct": row[1], "nulls": row[2],
            "min": row[3], "max": row[4],
        })

    registry.register(Tool(
        name="profile_column",
        description=(
            "Data profile of one column (row count, distinct, nulls, "
            "min/max). Simulate mode only. Use to test rename hypotheses: "
            "same profile = likely same data."
        ),
        input_schema=_obj(
            {"layer": {"type": "string"}, "table": {"type": "string"},
             "column": {"type": "string"}},
            ["layer", "table", "column"],
        ),
        fn=profile_column,
    ))

    def sample_rows(layer: str, table: str, limit: int = 5) -> str:
        limit = max(1, min(int(limit), 20))
        con = _duckdb()
        try:
            rel = f"{_qident(layer)}.{_qident(table)}"
            cur = con.execute(f"SELECT * FROM {rel} LIMIT {limit}")
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        finally:
            con.close()
        return _json({"columns": cols, "rows": rows})

    registry.register(Tool(
        name="sample_rows",
        description="Sample up to 20 rows from a table. Simulate mode only.",
        input_schema=_obj(
            {"layer": {"type": "string"}, "table": {"type": "string"},
             "limit": {"type": "integer"}},
            ["layer", "table"],
        ),
        fn=sample_rows,
    ))

    def run_sql(query: str) -> str:
        q = query.strip().rstrip(";")
        if ";" in q:
            return "ERROR: one statement only"
        if not re.match(r"^\s*(select|with)\b", q, re.IGNORECASE):
            return "ERROR: SELECT/WITH queries only"
        if _SQL_FORBIDDEN.search(q):
            return "ERROR: query contains a forbidden keyword (read-only tool)"
        con = _duckdb()
        try:
            cur = con.execute(f"SELECT * FROM ({q}) LIMIT 200")
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        finally:
            con.close()
        return _json({"columns": cols, "rows": rows, "row_cap": 200})

    registry.register(Tool(
        name="run_sql",
        description=(
            "Run one read-only SELECT/WITH query against the local medallion "
            "(schemas: bronze, silver, gold). Rows capped at 200. "
            "Simulate mode only."
        ),
        input_schema=_obj({"query": {"type": "string"}}, ["query"]),
        fn=run_sql,
    ))

    # ---------------- semantic model / reports ------------------------
    def grep_dax(pattern: str) -> str:
        model = ctx.backend.get_schema(Layer.SEMANTIC_MODEL)
        hits = []
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            return f"ERROR: bad regex: {exc}"
        for table in model.tables.values():
            for name, dax in table.measures.items():
                if rx.search(dax) or rx.search(name):
                    hits.append({"table": table.name, "measure": name, "dax": dax})
        return _json(hits)

    registry.register(Tool(
        name="grep_dax",
        description=(
            "Search all DAX measures (names + expressions) with a regex. "
            "Find which measures reference a column, e.g. pattern "
            "'Sales\\[Freight\\]'."
        ),
        input_schema=_obj({"pattern": {"type": "string"}}, ["pattern"]),
        fn=grep_dax,
    ))

    def read_report_metadata(report_name: str) -> str:
        reports = ctx.backend.get_schema(Layer.REPORTS)
        table = reports.tables.get(report_name)
        if table is None:
            return _json({
                "error": f"report {report_name!r} not found",
                "available": list(reports.tables),
            })
        return _json(table.to_dict())

    registry.register(Tool(
        name="read_report_metadata",
        description=(
            "Field bindings + path of one Power BI report (which model "
            "columns/measures each report uses)."
        ),
        input_schema=_obj({"report_name": {"type": "string"}}, ["report_name"]),
        fn=read_report_metadata,
    ))

    # ---------------- files / TMDL ------------------------------------
    def read_file(path: str) -> str:
        p = ctx.safe_path(path)
        if not p.exists():
            return f"ERROR: {path} does not exist"
        if p.stat().st_size > 200_000:
            return f"ERROR: {path} too large (>200KB)"
        return _clip(p.read_text(encoding="utf-8", errors="replace"))

    registry.register(Tool(
        name="read_file",
        description=(
            "Read a text file inside the repository (TMDL, config, PBIP "
            "definitions). Path is relative to the repo root; .env and .git "
            "are off limits."
        ),
        input_schema=_obj({"path": {"type": "string"}}, ["path"]),
        fn=read_file,
    ))

    def apply_tmdl_edit(file: str, find: str, replace: str) -> str:
        p = ctx.safe_path(file, root=ctx.reports_dir)
        if not p.exists():
            return f"ERROR: {file} not found under {ctx.reports_dir.name}/"
        if not p.is_file():
            return f"ERROR: {file} is not a regular file"
        if p.stat().st_size > 5 * 1024 * 1024:
            return f"ERROR: {file} exceeds the 5MB edit limit"
        text = p.read_text(encoding="utf-8")
        count = text.count(find)
        if count == 0:
            return f"ERROR: find-string not present in {file}"
        p.write_text(text.replace(find, replace), encoding="utf-8")
        return f"OK: replaced {count} occurrence(s) of {find!r} in {file}"

    registry.register(Tool(
        name="apply_tmdl_edit",
        description=(
            "Apply an exact find/replace edit to a TMDL/PBIP file under the "
            "configured reports directory. Verify with read_file first, and "
            "re-run run_diff afterwards to confirm the fix."
        ),
        input_schema=_obj(
            {"file": {"type": "string"}, "find": {"type": "string"},
             "replace": {"type": "string"}},
            ["file", "find", "replace"],
        ),
        fn=apply_tmdl_edit,
        writes=True,
    ))

    # ---------------- baselines / history ------------------------------
    def list_baselines() -> str:
        out = []
        for f in sorted(ctx.store.directory.glob("*.json")):
            payload = json.loads(f.read_text(encoding="utf-8"))
            out.append({
                "layer": f.stem,
                "captured_at": payload.get("captured_at"),
                "tables": len(payload.get("schema", {}).get("tables", {})),
            })
        history = sorted(
            p.name for p in (ctx.store.directory / "history").glob("*.json")
        ) if (ctx.store.directory / "history").exists() else []
        return _json({"current": out, "history_files": history[-50:]})

    registry.register(Tool(
        name="list_baselines",
        description=(
            "List current baseline snapshots (layer, captured_at, table "
            "count) and archived history snapshot filenames."
        ),
        input_schema=_obj({}),
        fn=list_baselines,
    ))

    def diff_snapshots(file_a: str, file_b: str) -> str:
        from ..backends.base import LayerSchema
        from ..schema_diff import diff_layer

        base_dir = ctx.store.directory
        pa = ctx.safe_path(file_a, root=base_dir)
        pb = ctx.safe_path(file_b, root=base_dir)
        if not pa.exists() or not pb.exists():
            return "ERROR: snapshot file(s) not found (see list_baselines)"
        a = LayerSchema.from_dict(json.loads(pa.read_text(encoding="utf-8"))["schema"])
        b = LayerSchema.from_dict(json.loads(pb.read_text(encoding="utf-8"))["schema"])
        if a.layer != b.layer:
            return "ERROR: snapshots are different layers"
        return _json([d.to_dict() for d in diff_layer(a, b)])

    registry.register(Tool(
        name="diff_snapshots",
        description=(
            "Diff two archived baseline snapshot files of the SAME layer "
            "(paths relative to the baseline dir, e.g. "
            "'history/silver-20260701T020000Z.json' vs 'silver.json'). "
            "Powers historical drift analysis."
        ),
        input_schema=_obj(
            {"file_a": {"type": "string"}, "file_b": {"type": "string"}},
            ["file_a", "file_b"],
        ),
        fn=diff_snapshots,
    ))

    # ---------------- git / PR ----------------------------------------
    def git_status() -> str:
        out = subprocess.run(
            ["git", "status", "--short", "--branch"],
            cwd=ctx.repo_dir, capture_output=True, text=True, timeout=30,
        )
        return _clip(out.stdout or out.stderr)

    registry.register(Tool(
        name="git_status",
        description="Current git branch + short status of the repository.",
        input_schema=_obj({}),
        fn=git_status,
    ))

    def read_pr_comments(pr_number: int) -> str:
        if shutil.which("gh") is None:
            return "ERROR: gh CLI not installed"
        out = subprocess.run(
            ["gh", "pr", "view", str(int(pr_number)),
             "--json", "title,body,comments,reviews"],
            cwd=ctx.repo_dir, capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            return f"ERROR: {out.stderr.strip()}"
        return _clip(out.stdout)

    registry.register(Tool(
        name="read_pr_comments",
        description=(
            "Read a GitHub pull request's title, body, comments and reviews "
            "(gh CLI required)."
        ),
        input_schema=_obj({"pr_number": {"type": "integer"}}, ["pr_number"]),
        fn=read_pr_comments,
    ))

    def git_commit_push(message: str) -> str:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=ctx.repo_dir, capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        base = ctx.cfg.get("git", {}).get("base_branch", "main")
        if branch in (base, "master"):
            return (
                f"ERROR: refusing to commit on base branch {branch!r}; "
                "check out a fix branch first"
            )
        add = subprocess.run(
            ["git", "add", "-A", str(ctx.reports_dir)],
            cwd=ctx.repo_dir, capture_output=True, text=True, timeout=60,
        )
        if add.returncode != 0:
            return f"ERROR: git add failed: {add.stderr.strip()}"
        commit = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=ctx.repo_dir, capture_output=True, text=True, timeout=60,
        )
        if commit.returncode != 0:
            return f"ERROR: git commit failed: {commit.stderr.strip() or commit.stdout.strip()}"
        push = subprocess.run(
            ["git", "push"],
            cwd=ctx.repo_dir, capture_output=True, text=True, timeout=120,
        )
        if push.returncode != 0:
            return f"committed locally, push failed: {push.stderr.strip()}"
        return f"OK: committed and pushed on {branch}: {message.splitlines()[0]}"

    registry.register(Tool(
        name="git_commit_push",
        description=(
            "Stage the reports directory, commit with the given message and "
            "push the CURRENT branch. Refuses to run on the base branch."
        ),
        input_schema=_obj({"message": {"type": "string"}}, ["message"]),
        fn=git_commit_push,
        writes=True,
    ))

    # ---------------- Fabric CLI ---------------------------------------
    def fab_run(args: str) -> str:
        from ..fabric_cli import FabricCLI

        tokens = args.split()
        if not tokens:
            return "ERROR: empty fab command"
        command = tokens[0].lower()
        if command in _FAB_WRITE_COMMANDS:
            if not ctx.allow_writes:
                return (
                    f"ERROR: 'fab {command}' creates/modifies Fabric items and "
                    "writes are disabled (--allow-writes)"
                )
        elif command not in _FAB_READ_COMMANDS:
            return (
                f"ERROR: 'fab {command}' not in allowlist "
                f"(read: {sorted(_FAB_READ_COMMANDS)}, "
                f"write: {sorted(_FAB_WRITE_COMMANDS)})"
            )
        cli = FabricCLI()
        if not cli.available():
            return "ERROR: fab CLI not installed (pip install ms-fabric-cli)"
        result = cli.run(*tokens, check=False)
        status = "OK" if result.returncode == 0 else f"EXIT {result.returncode}"
        return _clip(f"{status}\nstdout: {result.stdout}\nstderr: {result.stderr}")

    registry.register(Tool(
        name="fab_run",
        description=(
            "Run one Microsoft Fabric CLI command (space-separated args, "
            "no leading 'fab'). Read commands (ls/get/exists/auth) always "
            "allowed; create/mkdir/api/deploy require writes enabled."
        ),
        input_schema=_obj({"args": {"type": "string"}}, ["args"]),
        fn=fab_run,
        writes=False,  # per-command gating inside fn
    ))

    def update_config_ids(updates: str) -> str:
        import yaml

        allowed_keys = {
            "workspace_id", "lakehouse_id", "warehouse_id",
            "semantic_model_id", "sql_endpoint", "sql_database",
        }
        try:
            data = json.loads(updates)
        except json.JSONDecodeError as exc:
            return f"ERROR: updates must be a JSON object: {exc}"
        bad = set(data) - allowed_keys
        if bad:
            return f"ERROR: keys not allowed: {sorted(bad)} (only {sorted(allowed_keys)})"
        cfg_path = ctx.repo_dir / "config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        cfg.setdefault("fabric", {}).update({k: str(v) for k, v in data.items()})
        cfg_path.write_text(
            yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return f"OK: config.yaml fabric ids updated: {sorted(data)}"

    registry.register(Tool(
        name="update_config_ids",
        description=(
            "Write Fabric item IDs into config.yaml (JSON object; allowed "
            "keys: workspace_id, lakehouse_id, warehouse_id, "
            "semantic_model_id, sql_endpoint, sql_database)."
        ),
        input_schema=_obj({"updates": {"type": "string"}}, ["updates"]),
        fn=update_config_ids,
        writes=True,
    ))

    # ---------------- notifications ------------------------------------
    def preview_notification(channel: str, summary: str) -> str:
        from ..lineage import annotate_downstream
        from ..notifications.base import DriftAlert
        from ..notifications.outlook_channel import OutlookChannel
        from ..notifications.slack_channel import SlackChannel
        from ..notifications.teams_channel import TeamsChannel

        baselines = ctx.store.load_all()
        drifts = []
        if baselines:
            drifts = annotate_downstream(
                diff_all(baselines, ctx.backend.get_all_schemas()),
                ctx.graph, ctx.workspaces,
            )
        alert = DriftAlert(drifts=drifts, summary=summary, environment=ctx.mode)
        channels = {
            "slack": SlackChannel(webhook_url="https://example.invalid"),
            "teams": TeamsChannel(webhook_url="https://example.invalid"),
            "outlook": OutlookChannel(
                sender="preview@example.invalid", to=["preview@example.invalid"]
            ),
        }
        ch = channels.get(channel.lower())
        if ch is None:
            return f"ERROR: channel must be one of {sorted(channels)}"
        return _json(ch.render(alert))

    registry.register(Tool(
        name="preview_notification",
        description=(
            "Render (never send) the current drift state as a channel payload "
            "(slack|teams|outlook) using YOUR summary text — use to tailor "
            "messaging per audience."
        ),
        input_schema=_obj(
            {"channel": {"type": "string"}, "summary": {"type": "string"}},
            ["channel", "summary"],
        ),
        fn=preview_notification,
    ))

    # ---------------- filter to the agent's whitelist -------------------
    if allowed is not None:
        unknown = set(allowed) - set(registry.names())
        if unknown:
            raise ValueError(f"agent references unknown tools: {sorted(unknown)}")
        filtered = ToolRegistry(context)
        for name in allowed:
            filtered.register(registry._tools[name])
        return filtered
    return registry
