"""SAP HANA backend: catalog mapping, type map, contract compliance."""

from __future__ import annotations

import pytest

from fabric_drift_detective.backends.base import Layer
from fabric_drift_detective.backends.hana_backend import HANA_TYPE_MAP, HanaBackend
from tests.backends.backend_contract import SchemaBackendContract
from tests.backends.test_sql_catalog_base import FakeConnection

ROWS = [
    ("CUSTOMER", "ID", "INTEGER", "FALSE", 1),
    ("CUSTOMER", "NAME", "NVARCHAR(100)", "TRUE", 2),
    ("CUSTOMER", "BALANCE", "DECIMAL(19,4)", "TRUE", 3),
    ("CUSTOMER", "CREATED", "SECONDDATE", "TRUE", 4),
]


def _backend(rows=ROWS):
    return HanaBackend(
        {"schema": "ERP", "layer": "bronze"},
        connection_factory=lambda: FakeConnection(rows),
    )


def test_catalog_query_targets_sys_table_columns():
    backend = _backend()
    assert "SYS.TABLE_COLUMNS" in backend.catalog_query.sql
    assert backend.catalog_query.params == ("ERP",)


def test_rows_map_to_normalized_layer_schema():
    schema = _backend().get_schema(Layer.BRONZE)
    cols = schema.tables["CUSTOMER"].columns
    assert cols["ID"].dtype == "int" and cols["ID"].nullable is False
    assert cols["NAME"].dtype == "string(100)"
    assert cols["BALANCE"].dtype == "decimal(19,4)"
    assert cols["CREATED"].dtype == "timestamp"  # SECONDDATE is HANA-only


def test_hana_specific_types_covered():
    for hana_type in ("NVARCHAR", "SHORTTEXT", "ALPHANUM", "SECONDDATE",
                      "LONGDATE", "DAYDATE", "SMALLDECIMAL", "ST_GEOMETRY"):
        # ST_GEOMETRY deliberately NOT mapped (passthrough); rest must be
        if hana_type == "ST_GEOMETRY":
            assert hana_type not in HANA_TYPE_MAP
        else:
            assert hana_type in HANA_TYPE_MAP


def test_missing_schema_config_rejected():
    with pytest.raises(ValueError, match="schema"):
        HanaBackend({}, connection_factory=lambda: FakeConnection([]))


def test_missing_env_names_variables(monkeypatch):
    for var in ("HANA_HOST", "HANA_PORT", "HANA_USER", "HANA_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    backend = HanaBackend({"schema": "ERP"})  # default env-based factory
    with pytest.raises(OSError, match="HANA_HOST"):
        backend.get_schema(Layer.BRONZE)


def test_configurable_layer():
    backend = HanaBackend(
        {"schema": "ERP", "layer": "silver"},
        connection_factory=lambda: FakeConnection(ROWS),
    )
    assert backend.list_layers() == [Layer.SILVER]


class TestHanaBackendContract(SchemaBackendContract):
    @pytest.fixture
    def backend(self):
        return _backend()

    @pytest.fixture
    def empty_backend(self):
        return _backend(rows=[])
