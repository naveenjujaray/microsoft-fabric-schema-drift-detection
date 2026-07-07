"""Azure SQL / SQL Server backend: catalog mapping, type map, contract."""

from __future__ import annotations

import pytest

from fabric_drift_detective.backends.base import Layer
from fabric_drift_detective.backends.sqlserver_backend import (
    SQLSERVER_TYPE_MAP,
    SqlServerBackend,
)
from tests.backends.backend_contract import SchemaBackendContract
from tests.backends.test_sql_catalog_base import FakeConnection

ROWS = [
    ("Orders", "OrderID", "int", "NO", 1),
    ("Orders", "Note", "nvarchar", "YES", 2),
    ("Orders", "Amount", "decimal", "YES", 3),
    ("Orders", "CustomerRef", "uniqueidentifier", "YES", 4),
    ("Orders", "RowVer", "timestamp", "NO", 5),
]


def _backend(rows=ROWS):
    return SqlServerBackend(
        {"schema": "dbo", "layer": "bronze"},
        connection_factory=lambda: FakeConnection(rows),
    )


def test_catalog_query_targets_information_schema():
    backend = _backend()
    assert "INFORMATION_SCHEMA.COLUMNS" in backend.catalog_query.sql
    assert backend.catalog_query.params == ("dbo",)


def test_rows_map_to_normalized_layer_schema():
    schema = _backend().get_schema(Layer.BRONZE)
    cols = schema.tables["Orders"].columns
    assert cols["OrderID"].dtype == "int"
    assert cols["Note"].dtype == "string"
    assert cols["Amount"].dtype == "decimal"
    assert cols["CustomerRef"].dtype == "string"
    # SQL Server TIMESTAMP is rowversion (binary), not a temporal type
    assert cols["RowVer"].dtype == "binary"


def test_sqlserver_specific_types_covered():
    for mssql_type in ("UNIQUEIDENTIFIER", "XML", "NTEXT", "DATETIMEOFFSET",
                       "IMAGE", "ROWVERSION"):
        assert mssql_type in SQLSERVER_TYPE_MAP
    assert SQLSERVER_TYPE_MAP["TIMESTAMP"] == "binary"
    # CLR / spatial deliberately unmapped: passthrough + warning
    for special in ("HIERARCHYID", "GEOGRAPHY", "GEOMETRY", "SQL_VARIANT"):
        assert special not in SQLSERVER_TYPE_MAP


def test_odbc_value_escapes_metacharacters():
    from fabric_drift_detective.backends.sqlserver_backend import _odbc_value

    assert _odbc_value("plain") == "{plain}"
    # ';' must not terminate the attribute; '}' must be doubled
    assert _odbc_value("p;w0rd}x") == "{p;w0rd}}x}"


def test_missing_schema_config_rejected():
    with pytest.raises(ValueError, match="schema"):
        SqlServerBackend({}, connection_factory=lambda: FakeConnection([]))


def test_missing_env_names_variables(monkeypatch):
    for var in ("SQLSERVER_HOST", "SQLSERVER_DATABASE", "SQLSERVER_USER",
                "SQLSERVER_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    backend = SqlServerBackend({"schema": "dbo"})
    with pytest.raises(OSError, match="SQLSERVER_HOST"):
        backend.get_schema(Layer.BRONZE)


class TestSqlServerBackendContract(SchemaBackendContract):
    @pytest.fixture
    def backend(self):
        return _backend()

    @pytest.fixture
    def empty_backend(self):
        return _backend(rows=[])


def test_registry_builds_sqlserver_backend():
    from fabric_drift_detective.backends import make_source_backend

    backend = make_source_backend(
        {"type": "sqlserver", "schema": "dbo"},
        connection_factory=lambda: FakeConnection(ROWS),
    )
    assert isinstance(backend, SqlServerBackend)
