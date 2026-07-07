"""AWS Redshift backend: catalog mapping, type map, contract."""

from __future__ import annotations

import pytest

from fabric_drift_detective.backends.base import Layer
from fabric_drift_detective.backends.redshift_backend import (
    REDSHIFT_TYPE_MAP,
    RedshiftBackend,
)
from tests.backends.backend_contract import SchemaBackendContract
from tests.backends.test_sql_catalog_base import FakeConnection

ROWS = [
    ("orders", "order_id", "bigint", "NO", 1),
    ("orders", "note", "character varying", "YES", 2),
    ("orders", "amount", "numeric", "YES", 3),
    ("orders", "placed_at", "timestamp with time zone", "YES", 4),
]


def _backend(rows=ROWS):
    return RedshiftBackend(
        {"schema": "public", "layer": "bronze"},
        connection_factory=lambda: FakeConnection(rows),
    )


def test_catalog_query_targets_svv_columns():
    backend = _backend()
    assert "SVV_COLUMNS" in backend.catalog_query.sql
    assert backend.catalog_query.params == ("public",)


def test_rows_map_to_normalized_layer_schema():
    schema = _backend().get_schema(Layer.BRONZE)
    cols = schema.tables["orders"].columns
    assert cols["order_id"].dtype == "bigint"
    assert cols["note"].dtype == "string"
    assert cols["amount"].dtype == "decimal"
    assert cols["placed_at"].dtype == "timestamp"


def test_redshift_specific_types_covered():
    for rs_type in ("TIMESTAMPTZ", "TIMETZ", "TIME", "VARBYTE"):
        assert rs_type in REDSHIFT_TYPE_MAP
    # semi-structured / spatial deliberately unmapped
    for special in ("SUPER", "GEOMETRY", "GEOGRAPHY", "HLLSKETCH"):
        assert special not in REDSHIFT_TYPE_MAP


def test_missing_schema_config_rejected():
    with pytest.raises(ValueError, match="schema"):
        RedshiftBackend({}, connection_factory=lambda: FakeConnection([]))


def test_missing_env_names_variables(monkeypatch):
    for var in ("REDSHIFT_HOST", "REDSHIFT_DATABASE", "REDSHIFT_USER",
                "REDSHIFT_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    backend = RedshiftBackend({"schema": "public"})
    with pytest.raises(OSError, match="REDSHIFT_HOST"):
        backend.get_schema(Layer.BRONZE)


class TestRedshiftBackendContract(SchemaBackendContract):
    @pytest.fixture
    def backend(self):
        return _backend()

    @pytest.fixture
    def empty_backend(self):
        return _backend(rows=[])


def test_registry_builds_redshift_backend():
    from fabric_drift_detective.backends import make_source_backend

    backend = make_source_backend(
        {"type": "redshift", "schema": "public"},
        connection_factory=lambda: FakeConnection(ROWS),
    )
    assert isinstance(backend, RedshiftBackend)
