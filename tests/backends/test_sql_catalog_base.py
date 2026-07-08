"""SqlCatalogBackend: catalog rows -> LayerSchema, via a fake DBAPI conn."""

from __future__ import annotations

import pytest

from fabric_drift_detective.backends.base import Layer
from fabric_drift_detective.backends.sql_catalog_base import (
    CatalogQuery,
    SqlCatalogBackend,
)
from fabric_drift_detective.backends.type_normalize import ANSI_TYPE_MAP, TypeNormalizer
from tests.backends.backend_contract import SchemaBackendContract

ROWS = [
    # (table, column, dtype, nullable, ordinal)
    ("Customer", "CustomerID", "INTEGER", "NO", 1),
    ("Customer", "Email", "NVARCHAR(120)", "YES", 2),
    ("Orders", "OrderID", "BIGINT", "NO", 1),
    ("Orders", "Total", "DECIMAL(19,4)", "YES", 2),
]


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.executed.append((sql, tuple(params)))

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self, rows):
        self.cursor_obj = FakeCursor(rows)
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


def _backend(rows=ROWS, layer=Layer.BRONZE):
    connections: list[FakeConnection] = []

    def factory():
        con = FakeConnection(rows)
        connections.append(con)
        return con

    backend = SqlCatalogBackend(
        connection_factory=factory,
        catalog_query=CatalogQuery(
            sql="SELECT ... WHERE schema = ?", params=("MYSCHEMA",)
        ),
        normalizer=TypeNormalizer(ANSI_TYPE_MAP, source="fake"),
        layer=layer,
    )
    return backend, connections


def test_rows_become_layer_schema():
    backend, _ = _backend()
    schema = backend.get_schema(Layer.BRONZE)
    assert set(schema.tables) == {"Customer", "Orders"}
    email = schema.tables["Customer"].columns["Email"]
    assert email.dtype == "string(120)"   # normalized, params preserved
    assert email.nullable is True
    assert email.ordinal == 2
    cid = schema.tables["Customer"].columns["CustomerID"]
    assert cid.dtype == "int"
    assert cid.nullable is False


def test_query_executed_with_params_and_connection_closed():
    backend, connections = _backend()
    backend.get_schema(Layer.BRONZE)
    assert len(connections) == 1
    assert connections[0].closed
    sql, params = connections[0].cursor_obj.executed[0]
    assert params == ("MYSCHEMA",)


def test_wrong_layer_rejected():
    backend, _ = _backend()
    with pytest.raises(ValueError, match="gold"):
        backend.get_schema(Layer.GOLD)


def test_configurable_layer():
    backend, _ = _backend(layer=Layer.SILVER)
    assert backend.list_layers() == [Layer.SILVER]
    assert backend.get_schema(Layer.SILVER).layer is Layer.SILVER


def test_nullable_parsing_accepts_common_forms():
    rows = [
        ("T", "a", "INTEGER", "YES", 1),
        ("T", "b", "INTEGER", "TRUE", 2),
        ("T", "c", "INTEGER", True, 3),
        ("T", "d", "INTEGER", 1, 4),
        ("T", "e", "INTEGER", "NO", 5),
        ("T", "f", "INTEGER", False, 6),
    ]
    backend, _ = _backend(rows=rows)
    cols = backend.get_schema(Layer.BRONZE).tables["T"].columns
    assert [cols[c].nullable for c in "abcdef"] == [
        True, True, True, True, False, False,
    ]


def test_optional_default_and_flags_row_elements():
    """Backends opt into default/flags capture by widening their catalog
    query to 6 or 7 columns; 5-column rows keep working unchanged."""
    rows = [
        ("T", "a", "INTEGER", "NO", 1),
        ("T", "b", "INTEGER", "YES", 2, "42"),
        ("T", "c", "INTEGER", "YES", 3, None, "identity,computed"),
    ]
    backend, _ = _backend(rows=rows)
    cols = backend.get_schema(Layer.BRONZE).tables["T"].columns
    assert cols["a"].default is None and cols["a"].flags == ()
    assert cols["b"].default == "42" and cols["b"].flags == ()
    assert cols["c"].default is None
    assert cols["c"].flags == ("computed", "identity")  # sorted, deterministic


def test_zero_rows_logs_warning_but_returns_empty_schema(caplog):
    """Case-mismatched schema names match nothing; a silent empty baseline
    would make every future drift check pass vacuously — warn loudly."""
    import logging

    backend, _ = _backend(rows=[])
    with caplog.at_level(logging.WARNING):
        schema = backend.get_schema(Layer.BRONZE)
    assert schema.tables == {}  # contract: empty source never raises
    assert any("no columns" in r.message for r in caplog.records)
    assert any("MYSCHEMA" in r.message for r in caplog.records)


class TestSqlCatalogBackendContract(SchemaBackendContract):
    """The base itself passes the shared backend contract."""

    @pytest.fixture
    def backend(self):
        return _backend()[0]

    @pytest.fixture
    def empty_backend(self):
        return _backend(rows=[])[0]
