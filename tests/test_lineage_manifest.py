"""Lineage manifest loader: data-driven Bronze->Silver->Gold mappings."""

from __future__ import annotations

import json

import pytest

from fabric_drift_detective.backends.base import Layer
from fabric_drift_detective.lineage_manifest import (
    ColumnMapping,
    LineageManifest,
    LineageManifestError,
)
from fabric_drift_detective.medallion import (
    BRONZE_TO_SILVER,
    SILVER_TO_GOLD,
    build_lineage_graph,
)

VALID_YAML = """\
bronze_to_silver:
  - [Customer, CustomerID, customers, customer_id]
  - src_table: Customer
    src_column: EmailAddress
    dst_table: customers
    dst_column: email
silver_to_gold:
  - [customers, customer_id, Dim_Customer, CustomerKey]
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------
def test_valid_manifest_parses_both_entry_forms(tmp_path):
    manifest = LineageManifest.load(_write(tmp_path, "m.yaml", VALID_YAML))
    assert manifest.bronze_to_silver == [
        ColumnMapping("Customer", "CustomerID", "customers", "customer_id"),
        ColumnMapping("Customer", "EmailAddress", "customers", "email"),
    ]
    assert manifest.silver_to_gold == [
        ColumnMapping("customers", "customer_id", "Dim_Customer", "CustomerKey"),
    ]


def test_json_manifest_also_loads(tmp_path):
    payload = {
        "bronze_to_silver": [["T", "a", "t", "a2"]],
        "silver_to_gold": [["t", "a2", "G", "A"]],
    }
    manifest = LineageManifest.load(
        _write(tmp_path, "m.json", json.dumps(payload))
    )
    assert manifest.bronze_to_silver[0].dst_column == "a2"


def test_missing_file_names_path_no_stack_trace(tmp_path):
    missing = tmp_path / "nope.yaml"
    with pytest.raises(LineageManifestError, match="nope.yaml"):
        LineageManifest.load(missing)


def test_malformed_entry_names_offender(tmp_path):
    bad = "bronze_to_silver:\n  - [OnlyThree, items, here]\n"
    with pytest.raises(LineageManifestError, match="bronze_to_silver\\[0\\]"):
        LineageManifest.load(_write(tmp_path, "m.yaml", bad))


def test_mapping_entry_missing_key_names_offender(tmp_path):
    bad = (
        "silver_to_gold:\n"
        "  - [ok, ok, ok, ok]\n"
        "  - src_table: t\n    src_column: c\n    dst_table: g\n"
    )
    with pytest.raises(LineageManifestError, match="silver_to_gold\\[1\\]"):
        LineageManifest.load(_write(tmp_path, "m.yaml", bad))


def test_empty_string_in_entry_rejected(tmp_path):
    bad = 'bronze_to_silver:\n  - [T, "", t, c]\n'
    with pytest.raises(LineageManifestError, match="bronze_to_silver\\[0\\]"):
        LineageManifest.load(_write(tmp_path, "m.yaml", bad))


def test_non_mapping_top_level_rejected(tmp_path):
    with pytest.raises(LineageManifestError, match="mapping"):
        LineageManifest.load(_write(tmp_path, "m.yaml", "- just\n- a list\n"))


def test_unknown_section_rejected(tmp_path):
    bad = "gold_to_platinum:\n  - [a, b, c, d]\n"
    with pytest.raises(LineageManifestError, match="gold_to_platinum"):
        LineageManifest.load(_write(tmp_path, "m.yaml", bad))


# ---------------------------------------------------------------------------
# graph wiring
# ---------------------------------------------------------------------------
def test_manifest_edges_match_hand_declared(tmp_path):
    manifest = LineageManifest.load(_write(tmp_path, "m.yaml", VALID_YAML))
    graph = build_lineage_graph(manifest=manifest)
    edges = set(graph.edges())
    assert edges == {
        ("bronze:Customer.CustomerID", "silver:customers.customer_id"),
        ("bronze:Customer.EmailAddress", "silver:customers.email"),
        ("silver:customers.customer_id", "gold:Dim_Customer.CustomerKey"),
    }


def test_no_manifest_falls_back_to_demo_constants():
    graph = build_lineage_graph()
    expected = len(BRONZE_TO_SILVER) + len(SILVER_TO_GOLD)
    assert graph.edge_count == expected
    assert ("silver:customers.email", "gold:Dim_Customer.Email") in set(
        graph.edges()
    )


def test_manifest_layers_are_bronze_silver_gold(tmp_path):
    """Edges land on the canonical layer prefixes the drift engine expects."""
    manifest = LineageManifest.load(_write(tmp_path, "m.yaml", VALID_YAML))
    graph = build_lineage_graph(manifest=manifest)
    for src, dst in graph.edges():
        assert src.split(":", 1)[0] in (Layer.BRONZE.value, Layer.SILVER.value)
        assert dst.split(":", 1)[0] in (Layer.SILVER.value, Layer.GOLD.value)


def test_shipped_example_manifest_is_valid():
    manifest = LineageManifest.load("examples/lineage.example.yaml")
    assert manifest.bronze_to_silver and manifest.silver_to_gold
    graph = build_lineage_graph(manifest=manifest)
    assert graph.edge_count == len(manifest.bronze_to_silver) + len(
        manifest.silver_to_gold
    )
