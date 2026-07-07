"""Databricks/Unity Catalog backend: catalog mapping, type map, contract."""

from __future__ import annotations

import pytest

from fabric_drift_detective.backends.base import Layer
from fabric_drift_detective.backends.databricks_backend import (
    DATABRICKS_TYPE_MAP,
    DatabricksBackend,
)
from tests.backends.backend_contract import SchemaBackendContract
from tests.backends.test_sql_catalog_base import FakeConnection

ROWS = [
    ("orders", "order_id", "BIGINT", "NO", 1),
    ("orders", "note", "STRING", "YES", 2),
    ("orders", "amount", "DECIMAL(19,4)", "YES", 3),
    ("orders", "placed_at", "TIMESTAMP_NTZ", "YES", 4),
]


def _backend(rows=ROWS):
    return DatabricksBackend(
        {"catalog": "main", "schema": "sales", "layer": "bronze"},
        connection_factory=lambda: FakeConnection(rows),
    )


def test_catalog_query_targets_unity_information_schema():
    backend = _backend()
    assert "system.information_schema.columns" in backend.catalog_query.sql
    assert backend.catalog_query.params == ("main", "sales")


def test_rows_map_to_normalized_layer_schema():
    schema = _backend().get_schema(Layer.BRONZE)
    cols = schema.tables["orders"].columns
    assert cols["order_id"].dtype == "bigint"
    assert cols["note"].dtype == "string"
    assert cols["amount"].dtype == "decimal(19,4)"
    assert cols["placed_at"].dtype == "timestamp"


def test_databricks_specific_types_covered():
    for db_type in ("STRING", "TIMESTAMP_NTZ", "DOUBLE", "BIGINT"):
        assert db_type in DATABRICKS_TYPE_MAP
    # complex/semi-structured deliberately unmapped: passthrough + warning
    for complex_type in ("ARRAY", "MAP", "STRUCT", "VARIANT"):
        assert complex_type not in DATABRICKS_TYPE_MAP


def test_missing_catalog_config_rejected():
    with pytest.raises(ValueError, match="catalog"):
        DatabricksBackend(
            {"schema": "sales"}, connection_factory=lambda: FakeConnection([])
        )


def test_missing_schema_config_rejected():
    with pytest.raises(ValueError, match="schema"):
        DatabricksBackend(
            {"catalog": "main"}, connection_factory=lambda: FakeConnection([])
        )


def test_missing_env_names_variables(monkeypatch):
    for var in ("DATABRICKS_SERVER_HOSTNAME", "DATABRICKS_HTTP_PATH",
                "DATABRICKS_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    backend = DatabricksBackend({"catalog": "main", "schema": "sales"})
    with pytest.raises(OSError, match="DATABRICKS_SERVER_HOSTNAME"):
        backend.get_schema(Layer.BRONZE)


class TestDatabricksBackendContract(SchemaBackendContract):
    @pytest.fixture
    def backend(self):
        return _backend()

    @pytest.fixture
    def empty_backend(self):
        return _backend(rows=[])


def test_registry_builds_databricks_backend():
    from fabric_drift_detective.backends import make_source_backend

    backend = make_source_backend(
        {"type": "databricks", "catalog": "main", "schema": "sales"},
        connection_factory=lambda: FakeConnection(ROWS),
    )
    assert isinstance(backend, DatabricksBackend)
