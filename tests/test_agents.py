"""Agent layer tests: specs, tool guard rails, runtime loop. No network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from fabric_drift_detective.agents import AGENT_SPECS, list_agents, run_agent
from fabric_drift_detective.agents.runtime import (
    AgentRuntime,
    MockAgentRuntime,
    make_runtime,
)
from fabric_drift_detective.agents.tools import ToolContext, build_registry
from fabric_drift_detective.backends.base import Layer
from fabric_drift_detective.backends.local_backend import LocalBackend
from fabric_drift_detective.medallion import build_lineage_graph
from fabric_drift_detective.schema_store import SchemaStore


# ---------------------------------------------------------------- fixtures
@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    """Mini medallion + baselines + lineage graph in a temp dir."""
    db = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA bronze; CREATE SCHEMA silver; CREATE SCHEMA gold")
    con.execute("CREATE TABLE silver.customers (customer_id INTEGER, email VARCHAR)")
    con.execute("INSERT INTO silver.customers VALUES (1, 'a@b.c'), (2, 'd@e.f')")
    con.execute("CREATE TABLE gold.Dim_Customer (CustomerKey INTEGER, Email VARCHAR)")
    con.execute("CREATE TABLE bronze.Customer (CustomerID INTEGER, Email VARCHAR)")
    con.close()

    model = {"tables": [{
        "name": "Customer", "sourceTable": "Dim_Customer",
        "columns": [{"name": "CustomerKey", "dataType": "int64", "isKey": True},
                    {"name": "Email", "dataType": "string"}],
        "measures": [{"name": "Customers", "expression": "COUNTROWS(Customer)"}],
    }]}
    (tmp_path / "model.json").write_text(json.dumps(model))
    reports = {"reports": [{
        "name": "R1", "path": "x",
        "fields": [{"table": "Customer", "field": "Email", "kind": "column"}],
    }]}
    (tmp_path / "reports.json").write_text(json.dumps(reports))

    backend = LocalBackend(db, tmp_path / "model.json", tmp_path / "reports.json")
    store = SchemaStore(tmp_path / ".baselines")
    schemas = backend.get_all_schemas()
    store.save_all(schemas)
    graph = build_lineage_graph(
        schemas[Layer.SEMANTIC_MODEL], schemas[Layer.REPORTS]
    )

    reports_dir = tmp_path / "pbip_reports"
    reports_dir.mkdir()
    (reports_dir / "Customer.tmdl").write_text(
        "table Customer\n\tcolumn Email\n\t\tsourceColumn: email\n",
        encoding="utf-8",
    )
    cfg = {
        "simulate": {"db_path": str(db)},
        "git": {"reports_dir": "pbip_reports", "base_branch": "main"},
    }
    return ToolContext(
        cfg=cfg, mode="simulate", backend=backend, store=store, graph=graph,
        repo_dir=tmp_path, reports_dir=reports_dir, allow_writes=False,
    )


# ---------------------------------------------------------------- specs
def test_all_ten_agents_defined():
    assert len(AGENT_SPECS) == 10
    assert set(list_agents()) == set(AGENT_SPECS)


def test_every_spec_tools_resolve(ctx):
    """Every agent's tool whitelist must exist in the registry."""
    for spec in AGENT_SPECS.values():
        registry = build_registry(ctx, allowed=spec.tools)
        assert set(registry.names()) == set(spec.tools)


def test_unknown_tool_in_spec_raises(ctx):
    with pytest.raises(ValueError, match="unknown tools"):
        build_registry(ctx, allowed=("run_diff", "made_up_tool"))


# ---------------------------------------------------------------- guard rails
def test_write_tool_gated(ctx):
    registry = build_registry(ctx)
    out = registry.dispatch(
        "apply_tmdl_edit",
        {"file": "Customer.tmdl", "find": "email", "replace": "email_address"},
    )
    assert out.startswith("ERROR") and "write" in out.lower()
    # file untouched
    assert "sourceColumn: email\n" in (ctx.reports_dir / "Customer.tmdl").read_text()


def test_write_tool_allowed_when_enabled(ctx):
    ctx.allow_writes = True
    registry = build_registry(ctx)
    out = registry.dispatch(
        "apply_tmdl_edit",
        {"file": "Customer.tmdl", "find": "sourceColumn: email",
         "replace": "sourceColumn: email_address"},
    )
    assert out.startswith("OK")
    assert "email_address" in (ctx.reports_dir / "Customer.tmdl").read_text()


def test_sql_guard_blocks_dml(ctx):
    registry = build_registry(ctx)
    assert registry.dispatch(
        "run_sql", {"query": "DROP TABLE silver.customers"}
    ).startswith("ERROR")
    assert registry.dispatch(
        "run_sql", {"query": "SELECT 1; SELECT 2"}
    ).startswith("ERROR")
    assert registry.dispatch(
        "run_sql", {"query": "SELECT * FROM silver.customers WHERE 1=1 -- delete"}
    ).startswith("ERROR")  # forbidden keyword anywhere


def test_sql_select_works(ctx):
    registry = build_registry(ctx)
    out = json.loads(registry.dispatch(
        "run_sql", {"query": "SELECT count(*) AS n FROM silver.customers"}
    ))
    assert out["rows"][0][0] == 2


def test_path_sandbox(ctx):
    registry = build_registry(ctx)
    assert registry.dispatch(
        "read_file", {"path": "../outside.txt"}
    ).startswith("ERROR")
    assert registry.dispatch("read_file", {"path": ".env"}).startswith("ERROR")


def test_fab_write_command_gated(ctx, monkeypatch):
    registry = build_registry(ctx)
    out = registry.dispatch("fab_run", {"args": "create WS.Workspace/X.Lakehouse"})
    assert out.startswith("ERROR") and "--allow-writes" in out
    out = registry.dispatch("fab_run", {"args": "rm WS.Workspace"})
    assert out.startswith("ERROR") and "allowlist" in out


def test_unknown_tool_dispatch(ctx):
    registry = build_registry(ctx)
    assert registry.dispatch("nope", {}).startswith("ERROR: unknown tool")


# ---------------------------------------------------------------- tools
def test_query_lineage_downstream(ctx):
    registry = build_registry(ctx)
    out = json.loads(registry.dispatch(
        "query_lineage", {"node": "silver:customers.email"}
    ))
    assert "gold:Dim_Customer.Email" in out["impacted"]
    assert "reports:R1.Customer.Email" in out["impacted"]


def test_run_diff_detects_injected_drift(ctx):
    con = duckdb.connect(str(ctx.db_path))
    con.execute("ALTER TABLE silver.customers RENAME COLUMN email TO email_address")
    con.close()
    registry = build_registry(ctx)
    drifts = json.loads(registry.dispatch("run_diff", {}))
    types = {d["drift_type"] for d in drifts}
    assert "column_rename" in types and "cross_layer_break" in types


def test_profile_column(ctx):
    registry = build_registry(ctx)
    out = json.loads(registry.dispatch(
        "profile_column",
        {"layer": "silver", "table": "customers", "column": "email"},
    ))
    assert out["rows"] == 2 and out["nulls"] == 0


def test_list_baselines_and_history(ctx):
    registry = build_registry(ctx)
    out = json.loads(registry.dispatch("list_baselines", {}))
    layers = {b["layer"] for b in out["current"]}
    assert "silver" in layers
    assert out["history_files"]  # keep_history archived on save


def test_grep_dax(ctx):
    registry = build_registry(ctx)
    hits = json.loads(registry.dispatch("grep_dax", {"pattern": "COUNTROWS"}))
    assert hits[0]["measure"] == "Customers"


# ---------------------------------------------------------------- runtime
def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_block(name, tool_input, block_id="tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input,
                           id=block_id)


def _response(blocks, stop_reason, tokens=(10, 5)):
    return SimpleNamespace(
        content=blocks, stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]),
    )


class FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def _spec(name="lineage_qa"):
    return AGENT_SPECS[name]


def test_runtime_plain_answer(ctx, tmp_path):
    client = FakeClient([_response([_text_block("all clear")], "end_turn")])
    registry = build_registry(ctx, allowed=_spec().tools)
    rt = AgentRuntime(registry, client=client, log_dir=tmp_path / "logs")
    result = rt.run(_spec(), "any drift?")
    assert result.success and result.output == "all clear"
    assert result.turns == 1
    assert Path(result.log_path).exists()


def test_runtime_tool_loop(ctx, tmp_path):
    client = FakeClient([
        _response([_tool_block("query_lineage",
                               {"node": "silver:customers.email"})], "tool_use"),
        _response([_text_block("email feeds gold + report R1")], "end_turn"),
    ])
    registry = build_registry(ctx, allowed=_spec().tools)
    rt = AgentRuntime(registry, client=client, log_dir=tmp_path / "logs")
    result = rt.run(_spec(), "what breaks?")
    assert result.success and result.turns == 2
    assert result.tool_calls[0]["tool"] == "query_lineage"
    assert result.tool_calls[0]["ok"] is True
    # tool result fed back to the model
    second_call = client.calls[1]
    assert second_call["messages"][-1]["content"][0]["type"] == "tool_result"


def test_runtime_turn_cap(ctx, tmp_path):
    loop_resp = _response(
        [_tool_block("list_lineage_nodes", {"prefix": ""})], "tool_use"
    )
    client = FakeClient([loop_resp] * 5)
    registry = build_registry(ctx, allowed=_spec().tools)
    rt = AgentRuntime(registry, client=client, log_dir=tmp_path / "logs")
    result = rt.run(_spec(), "loop forever", max_turns=3)
    assert not result.success and result.stop_reason == "turn_cap"
    assert result.turns == 3


def test_runtime_token_budget(ctx, tmp_path):
    client = FakeClient([
        _response([_tool_block("list_lineage_nodes", {"prefix": ""})],
                  "tool_use", tokens=(900, 200)),
        _response([_text_block("late")], "end_turn"),
    ])
    registry = build_registry(ctx, allowed=_spec().tools)
    rt = AgentRuntime(registry, client=client, log_dir=tmp_path / "logs",
                      max_total_tokens=1000)
    result = rt.run(_spec(), "expensive")
    assert result.stop_reason == "token_budget"


def test_runtime_api_error_reported(ctx, tmp_path):
    class BoomClient:
        def __init__(self):
            self.messages = SimpleNamespace(create=self._boom)

        def _boom(self, **kwargs):
            raise RuntimeError("api down")

    registry = build_registry(ctx, allowed=_spec().tools)
    rt = AgentRuntime(registry, client=BoomClient(), log_dir=tmp_path / "logs")
    result = rt.run(_spec(), "hi")
    assert not result.success and result.stop_reason == "api_error"
    assert "api down" in result.output


def test_make_runtime_without_key_is_mock(ctx, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    registry = build_registry(ctx, allowed=_spec().tools)
    assert isinstance(make_runtime({}, registry), MockAgentRuntime)


def test_run_agent_offline_end_to_end(ctx, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = run_agent("triage", "rank the drift", ctx)
    assert not result.success and result.stop_reason == "no_api_key"
    assert "triage" in result.output


def test_run_agent_unknown_name_raises(ctx):
    with pytest.raises(KeyError):
        run_agent("nonexistent", "x", ctx)
