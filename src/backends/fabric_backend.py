"""Live Fabric backend: fab CLI first, REST fallback.

Maps real Fabric metadata into the same ``LayerSchema`` structures the
local backend produces, so the drift engine is backend-agnostic.

Layer sources:
    Bronze/Silver -> lakehouse tables (REST list-tables, or SQL
                     endpoint INFORMATION_SCHEMA when configured)
    Gold          -> warehouse via SQL endpoint INFORMATION_SCHEMA
    SemanticModel -> getDefinition TMDL parts, parsed minimally
    Reports       -> PBIP folder in the configured Git repo
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from ..fabric_cli import FabricCLI
from ..fabric_rest import FabricRest
from .base import ColumnSchema, Layer, LayerSchema, SchemaBackend, TableSchema

logger = logging.getLogger(__name__)

_TMDL_TABLE = re.compile(r"^table\s+'?([^'\n]+)'?", re.MULTILINE)
_TMDL_COLUMN = re.compile(
    r"^\tcolumn\s+'?([^'\n]+)'?[\s\S]*?dataType:\s*(\w+)", re.MULTILINE
)
_TMDL_MEASURE = re.compile(
    r"^\tmeasure\s+'?([^'\n=]+?)'?\s*=\s*(.+)$", re.MULTILINE
)
_TMDL_SOURCE_COLUMN = re.compile(r"sourceColumn:\s*(\S+)")


class FabricBackend(SchemaBackend):
    """Schema inspection against a real Fabric workspace."""

    def __init__(self, fabric_config: dict[str, Any],
                 reports_dir: str | Path = "pbip_reports") -> None:
        self.cfg = fabric_config
        self.workspace_id = fabric_config.get("workspace_id", "")
        self.lakehouse_id = fabric_config.get("lakehouse_id", "")
        self.warehouse_id = fabric_config.get("warehouse_id", "")
        self.semantic_model_id = fabric_config.get("semantic_model_id", "")
        self.reports_dir = Path(reports_dir)
        self.cli = FabricCLI()
        self.rest = FabricRest(api_base=fabric_config.get(
            "api_base", "https://api.fabric.microsoft.com/v1"))
        if not self.workspace_id:
            raise ValueError(
                "fabric.workspace_id missing in config.yaml - live mode "
                "needs real item IDs (see docs/FABRIC_SETUP.md)"
            )

    # ------------------------------------------------------------------
    def list_layers(self) -> list[Layer]:
        layers = [Layer.BRONZE, Layer.SILVER]
        if self.warehouse_id:
            layers.append(Layer.GOLD)
        if self.semantic_model_id:
            layers.append(Layer.SEMANTIC_MODEL)
        if self.reports_dir.exists():
            layers.append(Layer.REPORTS)
        return layers

    def get_schema(self, layer: Layer) -> LayerSchema:
        if layer in (Layer.BRONZE, Layer.SILVER):
            return self._lakehouse_layer(layer)
        if layer is Layer.GOLD:
            return self._warehouse_layer()
        if layer is Layer.SEMANTIC_MODEL:
            return self._semantic_model_layer()
        if layer is Layer.REPORTS:
            return self._reports_layer()
        raise ValueError(f"FabricBackend cannot inspect layer {layer}")

    # ------------------------------------------------------------------
    def _lakehouse_layer(self, layer: Layer) -> LayerSchema:
        """Lakehouse tables. Bronze/Silver are distinguished by a
        table-name prefix convention (``bronze_`` / ``silver_``) or by
        lakehouse schema when schemas are enabled."""
        sql_endpoint = self.cfg.get("sql_endpoint", "")
        if sql_endpoint:
            return self._sql_information_schema(
                layer, sql_endpoint, self.cfg.get("sql_database", ""),
                schema_filter=layer.value,
            )
        tables = self.rest.list_lakehouse_tables(
            self.workspace_id, self.lakehouse_id
        )
        prefix = f"{layer.value}_"
        result = LayerSchema(layer=layer)
        for t in tables:
            name = t.get("name", "")
            if not name.startswith(prefix) and t.get("schema", "") != layer.value:
                continue
            table = TableSchema(name=name.removeprefix(prefix))
            # REST list-tables has no column detail; SQL endpoint fills it.
            table.metadata["format"] = t.get("format", "")
            result.tables[table.name] = table
        return result

    def _warehouse_layer(self) -> LayerSchema:
        sql_endpoint = self.cfg.get("sql_endpoint", "")
        if not sql_endpoint:
            logger.warning("no sql_endpoint configured; Gold schema will be empty")
            return LayerSchema(layer=Layer.GOLD)
        return self._sql_information_schema(
            Layer.GOLD, sql_endpoint, self.cfg.get("sql_database", ""),
            schema_filter="dbo",
        )

    def _sql_information_schema(
        self, layer: Layer, endpoint: str, database: str, schema_filter: str
    ) -> LayerSchema:
        rows = self.rest.query_sql_endpoint(
            endpoint,
            database,
            "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, "
            "ORDINAL_POSITION FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = ? "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION",
            params=(schema_filter,),
        )
        result = LayerSchema(layer=layer)
        for table_name, col, dtype, nullable, ordinal in rows:
            table = result.tables.setdefault(table_name, TableSchema(name=table_name))
            table.columns[col] = ColumnSchema(
                name=col,
                dtype=str(dtype).upper(),
                nullable=str(nullable).upper() == "YES",
                ordinal=int(ordinal),
            )
        return result

    # ------------------------------------------------------------------
    def _semantic_model_layer(self) -> LayerSchema:
        """Parse TMDL parts from getDefinition into TableSchemas."""
        parts = self.rest.get_semantic_model_tmdl(
            self.workspace_id, self.semantic_model_id
        )
        result = LayerSchema(layer=Layer.SEMANTIC_MODEL)
        for path, text in parts.items():
            if "/tables/" not in path or not path.endswith(".tmdl"):
                continue
            m = _TMDL_TABLE.search(text)
            if not m:
                continue
            table = TableSchema(name=m.group(1).strip())
            for i, (col_name, dtype) in enumerate(_TMDL_COLUMN.findall(text)):
                table.columns[col_name.strip()] = ColumnSchema(
                    name=col_name.strip(), dtype=dtype.upper(), ordinal=i
                )
            for measure_name, dax in _TMDL_MEASURE.findall(text):
                table.measures[measure_name.strip()] = dax.strip()
            src = _TMDL_SOURCE_COLUMN.search(text)
            if src:
                table.metadata["source_table"] = src.group(1)
            result.tables[table.name] = table
        return result

    def _reports_layer(self) -> LayerSchema:
        """Scan PBIP report.json files for field bindings (best-effort)."""
        result = LayerSchema(layer=Layer.REPORTS)
        for report_dir in self.reports_dir.glob("*.Report"):
            table = TableSchema(name=report_dir.stem)
            table.metadata["path"] = str(report_dir)
            bindings: set[str] = set()
            for jf in report_dir.rglob("*.json"):
                try:
                    text = jf.read_text(encoding="utf-8")
                except OSError:
                    continue
                for match in re.finditer(
                    r'"Entity"\s*:\s*"([^"]+)"[\s\S]{0,200}?'
                    r'"Property"\s*:\s*"([^"]+)"',
                    text,
                ):
                    bindings.add(f"{match.group(1)}.{match.group(2)}")
            for i, b in enumerate(sorted(bindings)):
                table.columns[b] = ColumnSchema(name=b, dtype="COLUMN", ordinal=i)
            result.tables[table.name] = table
        return result
