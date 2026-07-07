"""Databricks / Unity Catalog direct-connect backend (upstream drift, mode A).

Reads ``system.information_schema.columns`` (Unity Catalog) so a
source-side rename/retype is caught BEFORE it reaches the Fabric
mirror/shortcut.

Driver: ``databricks-sql-connector`` — optional extra
(``pip install .[databricks]``), imported only inside the default
connection factory.

Config (``source:`` block in config.yaml)::

    mode: source
    source:
      type: databricks
      catalog: "main"        # Unity Catalog catalog
      schema: "sales"        # schema within the catalog
      layer: bronze          # medallion layer it feeds (default bronze)

Credentials via .env: ``DATABRICKS_SERVER_HOSTNAME``,
``DATABRICKS_HTTP_PATH``, ``DATABRICKS_TOKEN``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .base import Layer
from .sql_catalog_base import CatalogQuery, SqlCatalogBackend
from .type_normalize import ANSI_TYPE_MAP, TypeNormalizer

#: Databricks/Unity Catalog dialect names merged over the ANSI baseline
DATABRICKS_TYPE_MAP: dict[str, str] = {
    **ANSI_TYPE_MAP,
    "STRING": "string",
    "LONG": "bigint",
    "SHORT": "int",
    "BYTE": "int",
    "TIMESTAMP_NTZ": "timestamp",
    # ARRAY / MAP / STRUCT / VARIANT / INTERVAL intentionally unmapped:
    # complex columns pass through with a warning
}

_CATALOG_SQL = (
    "SELECT table_name, column_name, data_type, is_nullable, "
    "ordinal_position FROM system.information_schema.columns "
    "WHERE table_catalog = ? AND table_schema = ? "
    "ORDER BY table_name, ordinal_position"
)

_ENV_VARS = (
    "DATABRICKS_SERVER_HOSTNAME", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN",
)


def _env_connection_factory() -> Any:
    missing = [v for v in _ENV_VARS if not os.environ.get(v)]
    if missing:
        raise OSError(
            f"Databricks connection needs env var(s) {', '.join(missing)} "
            "(see .env.example; pip install .[databricks] for the driver)"
        )
    from databricks import sql  # optional extra: pip install .[databricks]

    return sql.connect(
        server_hostname=os.environ["DATABRICKS_SERVER_HOSTNAME"],
        http_path=os.environ["DATABRICKS_HTTP_PATH"],
        access_token=os.environ["DATABRICKS_TOKEN"],
    )


class DatabricksBackend(SqlCatalogBackend):
    """Snapshot one Unity Catalog schema as one medallion layer."""

    def __init__(
        self,
        source_config: dict[str, Any],
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        catalog = str(source_config.get("catalog", "")).strip()
        if not catalog:
            raise ValueError(
                "source.catalog is required for the Databricks backend"
            )
        schema = str(source_config.get("schema", "")).strip()
        if not schema:
            raise ValueError(
                "source.schema is required for the Databricks backend"
            )
        layer = Layer(str(source_config.get("layer", "bronze")))
        super().__init__(
            connection_factory=connection_factory or _env_connection_factory,
            catalog_query=CatalogQuery(sql=_CATALOG_SQL, params=(catalog, schema)),
            normalizer=TypeNormalizer(DATABRICKS_TYPE_MAP, source="databricks"),
            layer=layer,
        )
