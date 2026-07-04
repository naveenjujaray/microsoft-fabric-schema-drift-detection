"""Local simulation backend: DuckDB medallion + JSON semantic model/reports.

Lets the entire drift pipeline run with zero Fabric cost. The DuckDB
file contains three schemas (``bronze``, ``silver``, ``gold``) built
from AdventureWorksLT by ``sample_data/build_medallion.py``; the
semantic model and PBIP report metadata live in JSON files produced by
the same script.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from .base import ColumnSchema, Layer, LayerSchema, SchemaBackend, TableSchema

# DuckDB schema name per medallion layer
_DB_SCHEMAS = {
    Layer.BRONZE: "bronze",
    Layer.SILVER: "silver",
    Layer.GOLD: "gold",
}


class LocalBackend(SchemaBackend):
    """Reads schemas from a local DuckDB file plus JSON metadata files."""

    def __init__(
        self,
        db_path: str | Path,
        semantic_model_path: str | Path,
        reports_path: str | Path,
    ) -> None:
        self.db_path = Path(db_path)
        self.semantic_model_path = Path(semantic_model_path)
        self.reports_path = Path(reports_path)

    # ------------------------------------------------------------------
    def list_layers(self) -> list[Layer]:
        layers = [Layer.BRONZE, Layer.SILVER, Layer.GOLD]
        if self.semantic_model_path.exists():
            layers.append(Layer.SEMANTIC_MODEL)
        if self.reports_path.exists():
            layers.append(Layer.REPORTS)
        return layers

    def get_schema(self, layer: Layer) -> LayerSchema:
        if layer in _DB_SCHEMAS:
            return self._db_layer(layer)
        if layer is Layer.SEMANTIC_MODEL:
            return self._semantic_model_layer()
        if layer is Layer.REPORTS:
            return self._reports_layer()
        raise ValueError(f"LocalBackend cannot inspect layer {layer}")

    # ------------------------------------------------------------------
    def _db_layer(self, layer: Layer) -> LayerSchema:
        """Read table schemas from DuckDB information_schema."""
        schema_name = _DB_SCHEMAS[layer]
        con = duckdb.connect(str(self.db_path), read_only=True)
        try:
            rows = con.execute(
                """
                SELECT table_name, column_name, data_type,
                       is_nullable, ordinal_position
                FROM information_schema.columns
                WHERE table_schema = ?
                ORDER BY table_name, ordinal_position
                """,
                [schema_name],
            ).fetchall()
            keys = self._primary_keys(con, schema_name)
        finally:
            con.close()

        tables: dict[str, TableSchema] = {}
        for table_name, col, dtype, nullable, ordinal in rows:
            table = tables.setdefault(table_name, TableSchema(name=table_name))
            table.columns[col] = ColumnSchema(
                name=col,
                dtype=str(dtype).upper(),
                nullable=str(nullable).upper() == "YES",
                ordinal=int(ordinal),
                is_key=(table_name, col) in keys,
            )
        return LayerSchema(layer=layer, tables=tables)

    @staticmethod
    def _primary_keys(
        con: duckdb.DuckDBPyConnection, schema_name: str
    ) -> set[tuple[str, str]]:
        """(table, column) pairs that participate in a PRIMARY KEY."""
        rows = con.execute(
            """
            SELECT table_name, constraint_column_names
            FROM duckdb_constraints()
            WHERE schema_name = ? AND constraint_type = 'PRIMARY KEY'
            """,
            [schema_name],
        ).fetchall()
        keys: set[tuple[str, str]] = set()
        for table_name, cols in rows:
            for col in cols or []:
                keys.add((table_name, col))
        return keys

    # ------------------------------------------------------------------
    def _semantic_model_layer(self) -> LayerSchema:
        """Semantic model tables/measures from JSON (TMDL analogue)."""
        model = json.loads(self.semantic_model_path.read_text(encoding="utf-8"))
        tables: dict[str, TableSchema] = {}
        for t in model.get("tables", []):
            table = TableSchema(name=t["name"])
            for i, c in enumerate(t.get("columns", [])):
                table.columns[c["name"]] = ColumnSchema(
                    name=c["name"],
                    dtype=c.get("dataType", "string").upper(),
                    nullable=c.get("nullable", True),
                    ordinal=i,
                    is_key=c.get("isKey", False),
                )
            for m in t.get("measures", []):
                table.measures[m["name"]] = m.get("expression", "")
            table.metadata["source_table"] = t.get("sourceTable", "")
            tables[t["name"]] = table
        layer = LayerSchema(layer=Layer.SEMANTIC_MODEL, tables=tables)
        return layer

    def _reports_layer(self) -> LayerSchema:
        """PBIP report field usage from JSON metadata."""
        reports = json.loads(self.reports_path.read_text(encoding="utf-8"))
        tables: dict[str, TableSchema] = {}
        for r in reports.get("reports", []):
            table = TableSchema(name=r["name"])
            for i, f in enumerate(r.get("fields", [])):
                # a report "column" is table.column or table.measure it binds to
                name = f"{f['table']}.{f['field']}"
                table.columns[name] = ColumnSchema(
                    name=name, dtype=f.get("kind", "column").upper(), ordinal=i
                )
            table.metadata["path"] = r.get("path", "")
            tables[r["name"]] = table
        return LayerSchema(layer=Layer.REPORTS, tables=tables)
