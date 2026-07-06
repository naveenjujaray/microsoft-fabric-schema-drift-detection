"""Snowflake backend: catalog mapping, type map, contract compliance."""

from __future__ import annotations

import pytest

from fabric_drift_detective.backends.base import Layer
from fabric_drift_detective.backends.snowflake_backend import (
    SNOWFLAKE_TYPE_MAP,
    SnowflakeBackend,
)
from tests.backends.backend_contract import SchemaBackendContract
from tests.backends.test_sql_catalog_base import FakeConnection

ROWS = [
    ("ORDERS", "ORDER_ID", "NUMBER(38,0)", "NO", 1),
    ("ORDERS", "NOTE", "TEXT", "YES", 2),
    ("ORDERS", "AMOUNT", "NUMBER(19,4)", "YES", 3),
    ("ORDERS", "PLACED_AT", "TIMESTAMP_NTZ", "YES", 4),
]


def _backend(rows=ROWS):
    return SnowflakeBackend(
        {"schema": "PUBLIC", "layer": "bronze"},
        connection_factory=lambda: FakeConnection(rows),
    )


def test_catalog_query_targets_information_schema():
    backend = _backend()
    assert "INFORMATION_SCHEMA.COLUMNS" in backend.catalog_query.sql
    assert backend.catalog_query.params == ("PUBLIC",)


def test_rows_map_to_normalized_layer_schema():
    schema = _backend().get_schema(Layer.BRONZE)
    cols = schema.tables["ORDERS"].columns
    assert cols["ORDER_ID"].dtype == "decimal(38,0)"  # NUMBER -> decimal
    assert cols["NOTE"].dtype == "string"
    assert cols["AMOUNT"].dtype == "decimal(19,4)"
    assert cols["PLACED_AT"].dtype == "timestamp"


def test_snowflake_specific_types_covered():
    for sf_type in ("NUMBER", "STRING", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ",
                    "TIMESTAMP_TZ", "TEXT"):
        assert sf_type in SNOWFLAKE_TYPE_MAP
    # semi-structured deliberately unmapped: passthrough + warning
    for semi in ("VARIANT", "OBJECT", "ARRAY"):
        assert semi not in SNOWFLAKE_TYPE_MAP


def test_missing_schema_config_rejected():
    with pytest.raises(ValueError, match="schema"):
        SnowflakeBackend({}, connection_factory=lambda: FakeConnection([]))


def test_missing_env_names_variables(monkeypatch):
    for var in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD",
                "SNOWFLAKE_DATABASE"):
        monkeypatch.delenv(var, raising=False)
    backend = SnowflakeBackend({"schema": "PUBLIC"})
    with pytest.raises(OSError, match="SNOWFLAKE_ACCOUNT"):
        backend.get_schema(Layer.BRONZE)


class TestSnowflakeBackendContract(SchemaBackendContract):
    @pytest.fixture
    def backend(self):
        return _backend()

    @pytest.fixture
    def empty_backend(self):
        return _backend(rows=[])


# ---------------------------------------------------------------------------
# registry / auto-detect
# ---------------------------------------------------------------------------
def test_registry_builds_backends_by_type():
    from fabric_drift_detective.backends import SOURCE_BACKENDS, make_source_backend

    assert set(SOURCE_BACKENDS) >= {"hana", "snowflake"}
    backend = make_source_backend(
        {"type": "snowflake", "schema": "PUBLIC"},
        connection_factory=lambda: FakeConnection(ROWS),
    )
    assert isinstance(backend, SnowflakeBackend)


def test_registry_unknown_type_lists_available():
    from fabric_drift_detective.backends import make_source_backend

    with pytest.raises(ValueError, match="hana"):
        make_source_backend({"type": "oracle", "schema": "X"})


def test_registry_missing_type_rejected():
    from fabric_drift_detective.backends import make_source_backend

    with pytest.raises(ValueError, match="source.type"):
        make_source_backend({"schema": "X"})
