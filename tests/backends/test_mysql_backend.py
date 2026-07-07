"""MySQL / Aurora MySQL backend: catalog mapping, type map, contract."""

from __future__ import annotations

import pytest

from fabric_drift_detective.backends.base import Layer
from fabric_drift_detective.backends.mysql_backend import (
    MYSQL_TYPE_MAP,
    MySqlBackend,
)
from tests.backends.backend_contract import SchemaBackendContract
from tests.backends.test_sql_catalog_base import FakeConnection

ROWS = [
    ("orders", "order_id", "int", "NO", 1),
    ("orders", "note", "longtext", "YES", 2),
    ("orders", "amount", "decimal(19,4)", "YES", 3),
    ("orders", "placed_at", "datetime", "YES", 4),
    ("orders", "status", "enum", "YES", 5),
]


def _backend(rows=ROWS):
    return MySqlBackend(
        {"schema": "shop", "layer": "bronze"},
        connection_factory=lambda: FakeConnection(rows),
    )


def test_catalog_query_targets_information_schema():
    backend = _backend()
    assert "INFORMATION_SCHEMA.COLUMNS" in backend.catalog_query.sql
    assert backend.catalog_query.params == ("shop",)


def test_rows_map_to_normalized_layer_schema():
    schema = _backend().get_schema(Layer.BRONZE)
    cols = schema.tables["orders"].columns
    assert cols["order_id"].dtype == "int"
    assert cols["note"].dtype == "string"
    assert cols["amount"].dtype == "decimal(19,4)"
    assert cols["placed_at"].dtype == "timestamp"
    assert cols["status"].dtype == "string"  # enum -> string


def test_mysql_specific_types_covered():
    for my_type in ("MEDIUMINT", "TINYTEXT", "MEDIUMTEXT", "LONGTEXT",
                    "ENUM", "SET", "YEAR", "LONGBLOB"):
        assert my_type in MYSQL_TYPE_MAP
    # JSON / spatial deliberately unmapped: passthrough + warning
    for special in ("JSON", "GEOMETRY", "POINT"):
        assert special not in MYSQL_TYPE_MAP


def test_missing_schema_config_rejected():
    with pytest.raises(ValueError, match="schema"):
        MySqlBackend({}, connection_factory=lambda: FakeConnection([]))


def test_missing_env_names_variables(monkeypatch):
    for var in ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    backend = MySqlBackend({"schema": "shop"})
    with pytest.raises(OSError, match="MYSQL_HOST"):
        backend.get_schema(Layer.BRONZE)


class TestMySqlBackendContract(SchemaBackendContract):
    @pytest.fixture
    def backend(self):
        return _backend()

    @pytest.fixture
    def empty_backend(self):
        return _backend(rows=[])


def test_registry_builds_mysql_backend():
    from fabric_drift_detective.backends import make_source_backend

    backend = make_source_backend(
        {"type": "mysql", "schema": "shop"},
        connection_factory=lambda: FakeConnection(ROWS),
    )
    assert isinstance(backend, MySqlBackend)
