"""LocalBackend passes the shared SchemaBackend contract suite.

Also serves as the reference example for wiring the contract to a
concrete backend (see CONTRIBUTING.md).
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from src.backends.local_backend import LocalBackend
from tests.backends.backend_contract import SchemaBackendContract


def _make_db(path: Path, with_data: bool) -> None:
    con = duckdb.connect(str(path))
    for schema in ("bronze", "silver", "gold"):
        con.execute(f"CREATE SCHEMA {schema}")
    if with_data:
        con.execute(
            "CREATE TABLE bronze.Customer ("
            "CustomerID INTEGER PRIMARY KEY, Email VARCHAR)"
        )
        con.execute(
            "CREATE TABLE silver.customers ("
            "customer_id INTEGER NOT NULL, email VARCHAR)"
        )
        con.execute(
            "CREATE TABLE gold.Dim_Customer (CustomerKey INTEGER, Email VARCHAR)"
        )
    con.close()


def _make_backend(tmp_path: Path, with_data: bool) -> LocalBackend:
    db = tmp_path / "contract.duckdb"
    _make_db(db, with_data)
    model_path = tmp_path / "model.json"
    reports_path = tmp_path / "reports.json"
    if with_data:
        model_path.write_text(json.dumps({
            "tables": [{
                "name": "Customer",
                "sourceTable": "Dim_Customer",
                "columns": [{"name": "Email", "dataType": "string"}],
                "measures": [],
            }]
        }), encoding="utf-8")
        reports_path.write_text(json.dumps({
            "reports": [{
                "name": "R1", "path": "r1",
                "fields": [
                    {"table": "Customer", "field": "Email", "kind": "column"}
                ],
            }]
        }), encoding="utf-8")
    else:
        model_path.write_text(json.dumps({"tables": []}), encoding="utf-8")
        reports_path.write_text(json.dumps({"reports": []}), encoding="utf-8")
    return LocalBackend(
        db_path=db,
        semantic_model_path=model_path,
        reports_path=reports_path,
    )


class TestLocalBackendContract(SchemaBackendContract):
    @pytest.fixture
    def backend(self, tmp_path: Path) -> LocalBackend:
        return _make_backend(tmp_path, with_data=True)

    @pytest.fixture
    def empty_backend(self, tmp_path: Path) -> LocalBackend:
        return _make_backend(tmp_path, with_data=False)
