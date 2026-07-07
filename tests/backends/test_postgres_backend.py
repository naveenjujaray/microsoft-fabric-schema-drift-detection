"""PostgreSQL (RDS/Aurora) backend: catalog mapping, type map, contract."""

from __future__ import annotations

import pytest

from fabric_drift_detective.backends.base import Layer
from fabric_drift_detective.backends.postgres_backend import (
    POSTGRES_TYPE_MAP,
    PostgresBackend,
)
from tests.backends.backend_contract import SchemaBackendContract
from tests.backends.test_sql_catalog_base import FakeConnection

ROWS = [
    ("orders", "order_id", "integer", "NO", 1),
    ("orders", "note", "character varying", "YES", 2),
    ("orders", "amount", "numeric", "YES", 3),
    ("orders", "placed_at", "timestamp without time zone", "YES", 4),
    ("orders", "ref", "uuid", "YES", 5),
]


def _backend(rows=ROWS):
    return PostgresBackend(
        {"schema": "public", "layer": "bronze"},
        connection_factory=lambda: FakeConnection(rows),
    )


def test_catalog_query_targets_information_schema():
    backend = _backend()
    assert "information_schema.columns" in backend.catalog_query.sql
    assert backend.catalog_query.params == ("public",)


def test_rows_map_to_normalized_layer_schema():
    schema = _backend().get_schema(Layer.BRONZE)
    cols = schema.tables["orders"].columns
    assert cols["order_id"].dtype == "int"
    assert cols["note"].dtype == "string"
    assert cols["amount"].dtype == "decimal"
    assert cols["placed_at"].dtype == "timestamp"
    assert cols["ref"].dtype == "string"  # uuid -> string


def test_postgres_specific_types_covered():
    for pg_type in ("UUID", "TIME WITHOUT TIME ZONE", "TIME WITH TIME ZONE"):
        assert pg_type in POSTGRES_TYPE_MAP
    # semi-structured / user-defined deliberately unmapped
    for special in ("JSON", "JSONB", "ARRAY", "USER-DEFINED"):
        assert special not in POSTGRES_TYPE_MAP


def test_missing_schema_config_rejected():
    with pytest.raises(ValueError, match="schema"):
        PostgresBackend({}, connection_factory=lambda: FakeConnection([]))


def test_missing_env_names_variables(monkeypatch):
    for var in ("POSTGRES_HOST", "POSTGRES_DATABASE", "POSTGRES_USER",
                "POSTGRES_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    backend = PostgresBackend({"schema": "public"})
    with pytest.raises(OSError, match="POSTGRES_HOST"):
        backend.get_schema(Layer.BRONZE)


class TestPostgresBackendContract(SchemaBackendContract):
    @pytest.fixture
    def backend(self):
        return _backend()

    @pytest.fixture
    def empty_backend(self):
        return _backend(rows=[])


def test_registry_builds_postgres_backend():
    from fabric_drift_detective.backends import make_source_backend

    backend = make_source_backend(
        {"type": "postgres", "schema": "public"},
        connection_factory=lambda: FakeConnection(ROWS),
    )
    assert isinstance(backend, PostgresBackend)
