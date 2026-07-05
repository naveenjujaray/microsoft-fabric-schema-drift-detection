"""Cross-workspace lineage: cross_workspace_break synthesis + reasoning."""

from __future__ import annotations

from src.backends.base import ColumnSchema, Layer
from src.lineage import LineageGraph, annotate_downstream
from src.llm_reasoner import CROSS_WORKSPACE_SENTENCE, MockReasoner
from src.schema_diff import DriftRecord, DriftType, Severity, diff_layer
from src.workspace import WorkspaceRegistry

from .test_workspace import MANIFEST


def _registry() -> WorkspaceRegistry:
    return WorkspaceRegistry.from_manifest(MANIFEST)


def _graph() -> LineageGraph:
    """silver (Ingestion) -> gold (EDW) -> model (EDW) -> report (Reporting)."""
    g = LineageGraph()
    g.add_edge("silver:customers.email", "gold:Dim_Customer.Email")
    g.add_edge("gold:Dim_Customer.Email", "semantic_model:Customer.Email")
    g.add_edge(
        "semantic_model:Customer.Email",
        "reports:Customer Detail.Customer.Email",
    )
    return g


def _drop_drift() -> DriftRecord:
    return DriftRecord(
        layer=Layer.SILVER,
        drift_type=DriftType.COLUMN_DROP,
        severity=Severity.CRITICAL,
        table="customers",
        column="email",
        old="VARCHAR",
    )


def test_cross_workspace_breaks_synthesized():
    drifts = annotate_downstream([_drop_drift()], _graph(), _registry())
    ws_breaks = [
        d for d in drifts if d.drift_type is DriftType.CROSS_WORKSPACE_BREAK
    ]
    # gold + semantic_model live in EDW, report in Reporting: all three
    # downstream targets are outside the Ingestion workspace
    assert len(ws_breaks) == 3
    assert {d.workspace for d in ws_breaks} == {"EDW", "Reporting"}
    # no plain cross_layer_break remains for the re-classified targets
    assert not any(
        d.drift_type is DriftType.CROSS_LAYER_BREAK for d in drifts
    )


def test_cross_workspace_break_names_link_type():
    drifts = annotate_downstream([_drop_drift()], _graph(), _registry())
    gold_break = next(
        d for d in drifts
        if d.drift_type is DriftType.CROSS_WORKSPACE_BREAK
        and d.layer is Layer.GOLD
    )
    assert "via onelake_shortcut" in str(gold_break.new)
    assert "in workspace Ingestion" in str(gold_break.old)


def test_tenant_boundary_noted():
    drifts = annotate_downstream([_drop_drift()], _graph(), _registry())
    report_break = next(
        d for d in drifts
        if d.drift_type is DriftType.CROSS_WORKSPACE_BREAK
        and d.layer is Layer.REPORTS
    )
    # Reporting is tenant-2, Ingestion tenant-1
    assert "crosses tenant boundary" in str(report_break.new)


def test_source_drift_stamped_with_workspace():
    drifts = annotate_downstream([_drop_drift()], _graph(), _registry())
    assert drifts[0].workspace == "Ingestion"


def test_without_registry_behavior_unchanged():
    drifts = annotate_downstream([_drop_drift()], _graph(), None)
    assert {d.drift_type for d in drifts[1:]} == {DriftType.CROSS_LAYER_BREAK}
    assert all(d.workspace is None for d in drifts)


def test_same_workspace_targets_stay_cross_layer():
    """gold -> semantic_model both live in EDW: cross_layer_break."""
    g = LineageGraph()
    g.add_edge("gold:Dim_Customer.Email", "semantic_model:Customer.Email")
    drift = DriftRecord(
        layer=Layer.GOLD, drift_type=DriftType.COLUMN_DROP,
        severity=Severity.CRITICAL, table="Dim_Customer", column="Email",
    )
    drifts = annotate_downstream([drift], g, _registry())
    breaks = [d for d in drifts if d is not drift]
    assert len(breaks) == 1
    assert breaks[0].drift_type is DriftType.CROSS_LAYER_BREAK
    assert breaks[0].workspace == "EDW"


def test_mock_reasoner_mentions_multiple_workspaces():
    reg = _registry()
    drifts = annotate_downstream([_drop_drift()], _graph(), reg)
    impact = MockReasoner(workspaces=reg).analyze_impact(drifts)
    assert CROSS_WORKSPACE_SENTENCE in impact["summary"]
    assert "EDW" in impact["summary"] and "Reporting" in impact["summary"]


def test_mock_reasoner_includes_workspace_details():
    reg = _registry()
    drifts = annotate_downstream([_drop_drift()], _graph(), reg)
    impact = MockReasoner(workspaces=reg).analyze_impact(drifts)
    first = impact["analyses"][0]
    assert first["workspace"] == "Ingestion"
    assert first["workspace_path"].startswith("Ingestion / RawLake (Lakehouse)")
    radius = {
        entry["workspace"]: entry["assets"]
        for entry in first["affected_workspaces"]
    }
    assert radius == {"EDW": 2, "Reporting": 1}


def test_mock_reasoner_single_workspace_no_sentence():
    drift = _drop_drift()
    drift.downstream_impact = []
    impact = MockReasoner().analyze_impact([drift])
    assert CROSS_WORKSPACE_SENTENCE not in impact["summary"]


def test_end_to_end_rename_produces_cross_workspace_break(silver_baseline):
    """diff -> annotate with registry: full pipeline path."""
    import copy

    cur = copy.deepcopy(silver_baseline)
    col = cur.tables["customers"].columns.pop("email")
    cur.tables["customers"].columns["email_address"] = ColumnSchema(
        name="email_address", dtype=col.dtype, nullable=col.nullable,
        ordinal=col.ordinal,
    )
    drifts = diff_layer(silver_baseline, cur)
    drifts = annotate_downstream(drifts, _graph(), _registry())
    types = {d.drift_type for d in drifts}
    assert DriftType.COLUMN_RENAME in types
    assert DriftType.CROSS_WORKSPACE_BREAK in types
    rename_break = next(
        d for d in drifts if d.drift_type is DriftType.CROSS_WORKSPACE_BREAK
    )
    assert rename_break.auto_fixable  # renames stay mechanically fixable
