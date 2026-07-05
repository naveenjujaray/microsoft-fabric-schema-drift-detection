"""Shared base for SQL-catalog source backends.

Nearly every warehouse/database exposes an ``INFORMATION_SCHEMA`` or a
system catalog view. This base turns that observation into a framework:
a concrete backend supplies only

* a **connection factory** — zero-arg callable returning a DBAPI
  connection (import the driver inside the factory so it stays an
  optional dependency),
* a **catalog query** — SQL + bind params returning rows of
  ``(table_name, column_name, data_type, is_nullable, ordinal)``,
* a **type normalizer** — the source dialect's map into the canonical
  type vocabulary (see ``type_normalize``).

The result is a fully contract-compliant ``SchemaBackend`` (see
``tests/backends/backend_contract.py``) mapping the upstream source to
one medallion layer — almost always Bronze, because direct-connect
sources sit upstream of Fabric and drift is caught at the door.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .base import ColumnSchema, Layer, LayerSchema, SchemaBackend, TableSchema
from .type_normalize import TypeNormalizer

#: forms of "nullable" seen across catalogs
_TRUTHY = {"YES", "TRUE", "Y", "1"}


def _parse_nullable(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().upper() in _TRUTHY


@dataclass(frozen=True)
class CatalogQuery:
    """One catalog query returning (table, column, dtype, nullable, ordinal)."""

    sql: str
    params: tuple[Any, ...] = ()


class SqlCatalogBackend(SchemaBackend):
    """SchemaBackend over any DBAPI source with a queryable catalog."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        catalog_query: CatalogQuery,
        normalizer: TypeNormalizer,
        layer: Layer = Layer.BRONZE,
    ) -> None:
        self._connection_factory = connection_factory
        self.catalog_query = catalog_query
        self.normalizer = normalizer
        self.layer = layer

    # ------------------------------------------------------------------
    def list_layers(self) -> list[Layer]:
        return [self.layer]

    def get_schema(self, layer: Layer) -> LayerSchema:
        if layer is not self.layer:
            raise ValueError(
                f"{type(self).__name__} serves layer {self.layer.value!r}, "
                f"not {layer.value!r}"
            )
        rows = self._catalog_rows()
        result = LayerSchema(layer=self.layer)
        for table_name, column_name, dtype, nullable, ordinal in rows:
            table = result.tables.setdefault(
                str(table_name), TableSchema(name=str(table_name))
            )
            table.columns[str(column_name)] = ColumnSchema(
                name=str(column_name),
                dtype=self.normalizer.normalize(str(dtype)),
                nullable=_parse_nullable(nullable),
                ordinal=int(ordinal),
            )
        return result

    # ------------------------------------------------------------------
    def _catalog_rows(self) -> list[tuple[Any, ...]]:
        """Run the catalog query on a fresh connection; always close it."""
        con = self._connection_factory()
        try:
            cur = con.cursor()
            if self.catalog_query.params:
                cur.execute(self.catalog_query.sql, self.catalog_query.params)
            else:
                cur.execute(self.catalog_query.sql)
            return list(cur.fetchall())
        finally:
            con.close()
