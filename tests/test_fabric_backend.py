"""FabricBackend: gold_source warehouse|lakehouse generalization."""

from __future__ import annotations

import logging

import pytest

from src.backends.base import Layer
from src.backends.fabric_backend import FabricBackend

GOLD_ROWS = [
    ("Dim_Customer", "CustomerKey", "int", "NO", 1),
    ("Dim_Customer", "Email", "varchar", "YES", 2),
]


def _cfg(**overrides):
    cfg = {
        "workspace_id": "ws-1",
        "lakehouse_id": "lh-1",
        "warehouse_id": "wh-1",
        "sql_endpoint": "endpoint.fabric",
        "sql_database": "db",
    }
    cfg.update(overrides)
    return cfg


class FakeRest:
    def __init__(self):
        self.queries: list[tuple[str, tuple]] = []

    def query_sql_endpoint(self, endpoint, database, query, params=()):
        self.queries.append((query, tuple(params)))
        return GOLD_ROWS

    def list_lakehouse_tables(self, workspace_id, lakehouse_id):
        return [{"name": "gold_Dim_Customer", "format": "delta"}]


def _backend(**overrides) -> FabricBackend:
    backend = FabricBackend(_cfg(**overrides))
    backend.rest = FakeRest()  # type: ignore[assignment]
    return backend


def test_default_gold_source_is_warehouse_dbo():
    backend = _backend()
    schema = backend.get_schema(Layer.GOLD)
    assert schema.layer is Layer.GOLD
    assert "Dim_Customer" in schema.tables
    query, params = backend.rest.queries[0]  # type: ignore[attr-defined]
    assert params == ("dbo",)  # warehouse star schema lives in dbo


def test_gold_source_lakehouse_reads_gold_schema():
    backend = _backend(gold_source="lakehouse")
    schema = backend.get_schema(Layer.GOLD)
    assert "Dim_Customer" in schema.tables
    assert schema.tables["Dim_Customer"].columns["Email"].dtype == "VARCHAR"
    query, params = backend.rest.queries[0]  # type: ignore[attr-defined]
    assert params == ("gold",)  # lakehouse SQL endpoint, gold schema


def test_gold_source_lakehouse_without_endpoint_uses_rest_tables():
    backend = _backend(gold_source="lakehouse", sql_endpoint="")
    schema = backend.get_schema(Layer.GOLD)
    # REST list-tables has no column detail but the table is discovered
    assert "Dim_Customer" in schema.tables


def test_gold_in_layers_when_lakehouse_gold_configured():
    backend = _backend(gold_source="lakehouse", warehouse_id="")
    assert Layer.GOLD in backend.list_layers()


def test_gold_absent_when_warehouse_mode_has_no_warehouse():
    backend = _backend(warehouse_id="")
    assert Layer.GOLD not in backend.list_layers()


def test_no_endpoint_warehouse_mode_warns_and_returns_empty(caplog):
    backend = _backend(sql_endpoint="")
    with caplog.at_level(logging.WARNING):
        schema = backend.get_schema(Layer.GOLD)
    assert schema.tables == {}
    assert "sql_endpoint" in caplog.text


def test_invalid_gold_source_rejected():
    with pytest.raises(ValueError, match="gold_source"):
        FabricBackend(_cfg(gold_source="datamart"))
