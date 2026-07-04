"""LocalBackend integration tests against a temp DuckDB medallion."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from src.backends.base import Layer
from src.backends.local_backend import LocalBackend


@pytest.fixture
def backend(tmp_path: Path) -> LocalBackend:
    db = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA bronze")
    con.execute("CREATE SCHEMA silver")
    con.execute("CREATE SCHEMA gold")
    con.execute("""
        CREATE TABLE bronze.Customer (
            CustomerID INTEGER PRIMARY KEY,
            Email VARCHAR
        )""")
    con.execute("""
        CREATE TABLE silver.customers (
            customer_id INTEGER NOT NULL,
            email VARCHAR
        )""")
    con.execute("""
        CREATE TABLE gold.Dim_Customer (
            CustomerKey INTEGER,
            Email VARCHAR
        )""")
    con.close()

    model = {
        "tables": [
            {
                "name": "Customer",
                "sourceTable": "Dim_Customer",
                "columns": [
                    {"name": "CustomerKey", "dataType": "int64", "isKey": True},
                    {"name": "Email", "dataType": "string"},
                ],
                "measures": [
                    {"name": "Customers", "expression": "COUNTROWS(Customer)"}
                ],
            }
        ]
    }
    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps(model))

    reports = {
        "reports": [
            {
                "name": "R1",
                "path": "x",
                "fields": [
                    {"table": "Customer", "field": "Email", "kind": "column"},
                    {"table": "Customer", "field": "Customers", "kind": "measure"},
                ],
            }
        ]
    }
    reports_path = tmp_path / "reports.json"
    reports_path.write_text(json.dumps(reports))

    return LocalBackend(db, model_path, reports_path)


def test_lists_all_five_layers(backend):
    assert backend.list_layers() == [
        Layer.BRONZE, Layer.SILVER, Layer.GOLD,
        Layer.SEMANTIC_MODEL, Layer.REPORTS,
    ]


def test_bronze_schema_types_and_keys(backend):
    schema = backend.get_schema(Layer.BRONZE)
    customer = schema.tables["Customer"]
    assert customer.columns["CustomerID"].dtype == "INTEGER"
    assert customer.columns["CustomerID"].is_key is True
    assert customer.columns["Email"].nullable is True


def test_silver_not_null_detected(backend):
    schema = backend.get_schema(Layer.SILVER)
    assert schema.tables["customers"].columns["customer_id"].nullable is False


def test_semantic_model_measures(backend):
    schema = backend.get_schema(Layer.SEMANTIC_MODEL)
    table = schema.tables["Customer"]
    assert table.measures["Customers"] == "COUNTROWS(Customer)"
    assert table.metadata["source_table"] == "Dim_Customer"
    assert table.columns["CustomerKey"].is_key is True


def test_reports_bindings(backend):
    schema = backend.get_schema(Layer.REPORTS)
    r1 = schema.tables["R1"]
    assert "Customer.Email" in r1.columns
    assert r1.columns["Customer.Customers"].dtype == "MEASURE"


def test_roundtrip_serialization(backend):
    schema = backend.get_schema(Layer.BRONZE)
    from src.backends.base import LayerSchema

    restored = LayerSchema.from_dict(schema.to_dict())
    assert restored.to_dict() == schema.to_dict()
