"""Lineage graph + cross-layer break tests (the differentiator)."""

from __future__ import annotations

from fabric_drift_detective.backends.base import (
    ColumnSchema,
    Layer,
    LayerSchema,
    TableSchema,
)
from fabric_drift_detective.lineage import LineageGraph, annotate_downstream, node_id
from fabric_drift_detective.schema_diff import DriftRecord, DriftType, Severity


def _graph() -> LineageGraph:
    g = LineageGraph()
    # silver.customers.email -> gold.Dim_Customer.Email
    g.add_mapping(Layer.SILVER, "customers", "email",
                  Layer.GOLD, "Dim_Customer", "Email")
    # gold -> semantic model column
    g.add_edge(
        node_id(Layer.GOLD, "Dim_Customer", "Email"),
        node_id(Layer.SEMANTIC_MODEL, "Customer", "Email"),
    )
    # model column -> report binding
    g.add_edge(
        node_id(Layer.SEMANTIC_MODEL, "Customer", "Email"),
        node_id(Layer.REPORTS, "Customer Detail", "Customer.Email"),
    )
    return g


def test_downstream_walk_crosses_all_layers():
    g = _graph()
    down = g.downstream(node_id(Layer.SILVER, "customers", "email"))
    assert down == [
        "gold:Dim_Customer.Email",
        "semantic_model:Customer.Email",
        "reports:Customer Detail.Customer.Email",
    ]


def test_upstream_walk():
    g = _graph()
    up = g.upstream(node_id(Layer.REPORTS, "Customer Detail", "Customer.Email"))
    assert "silver:customers.email" in up


def test_annotate_creates_cross_layer_breaks():
    g = _graph()
    drift = DriftRecord(
        layer=Layer.SILVER,
        drift_type=DriftType.COLUMN_RENAME,
        severity=Severity.CRITICAL,
        table="customers",
        column="email",
        old="email",
        new="email_address",
        auto_fixable=True,
    )
    result = annotate_downstream([drift], g)

    assert drift.downstream_impact == [
        "gold:Dim_Customer.Email",
        "semantic_model:Customer.Email",
        "reports:Customer Detail.Customer.Email",
    ]
    breaks = [d for d in result if d.drift_type is DriftType.CROSS_LAYER_BREAK]
    assert {b.layer for b in breaks} == {
        Layer.GOLD, Layer.SEMANTIC_MODEL, Layer.REPORTS
    }
    assert all(b.severity is Severity.CRITICAL for b in breaks)
    # rename-driven breaks are mechanically fixable
    assert all(b.auto_fixable for b in breaks)


def test_info_drift_creates_no_breaks():
    g = _graph()
    drift = DriftRecord(
        layer=Layer.SILVER,
        drift_type=DriftType.COLUMN_ADD,
        severity=Severity.INFO,
        table="customers",
        column="email",  # even though it has downstream edges
    )
    result = annotate_downstream([drift], g)
    assert all(d.drift_type is not DriftType.CROSS_LAYER_BREAK for d in result)


def test_measure_edges_from_dax():
    model = LayerSchema(layer=Layer.SEMANTIC_MODEL)
    sales = TableSchema(name="Sales")
    sales.columns["LineTotal"] = ColumnSchema(name="LineTotal", dtype="DECIMAL")
    sales.measures["Total Revenue"] = "SUM(Sales[LineTotal])"
    sales.metadata["source_table"] = "Fact_Sales"
    model.tables["Sales"] = sales

    g = LineageGraph()
    g.register_semantic_model(model)

    down = g.downstream(node_id(Layer.GOLD, "Fact_Sales", "LineTotal"))
    assert "semantic_model:Sales.LineTotal" in down
    assert "semantic_model:Sales#Total Revenue" in down


def test_table_drop_impacts_columns_downstream():
    g = _graph()
    drift = DriftRecord(
        layer=Layer.SILVER,
        drift_type=DriftType.TABLE_DROP,
        severity=Severity.CRITICAL,
        table="customers",
    )
    result = annotate_downstream([drift], g)
    assert "gold:Dim_Customer.Email" in drift.downstream_impact
    breaks = [d for d in result if d.drift_type is DriftType.CROSS_LAYER_BREAK]
    assert breaks  # table drop must surface downstream breakage
