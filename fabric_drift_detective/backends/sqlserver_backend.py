"""Azure SQL / SQL Server direct-connect backend (upstream drift, mode A).

Reads ``INFORMATION_SCHEMA.COLUMNS`` so a source-side rename/retype is
caught BEFORE the nightly load lands it in Fabric.

Driver: ``pyodbc`` — optional extra (``pip install .[sqlserver]``),
imported only inside the default connection factory. Needs a Microsoft
ODBC driver on the host (default: "ODBC Driver 18 for SQL Server";
override with ``SQLSERVER_DRIVER``).

Config (``source:`` block in config.yaml)::

    mode: source
    source:
      type: sqlserver
      schema: "dbo"          # SQL Server schema to snapshot
      layer: bronze          # medallion layer it feeds (default bronze)

Credentials via .env: ``SQLSERVER_HOST``, ``SQLSERVER_DATABASE``,
``SQLSERVER_USER``, ``SQLSERVER_PASSWORD`` (optional:
``SQLSERVER_PORT`` (1433), ``SQLSERVER_DRIVER``).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .base import Layer
from .sql_catalog_base import CatalogQuery, SqlCatalogBackend
from .type_normalize import ANSI_TYPE_MAP, TypeNormalizer

#: SQL Server dialect names merged over the ANSI baseline
SQLSERVER_TYPE_MAP: dict[str, str] = {
    **ANSI_TYPE_MAP,
    "UNIQUEIDENTIFIER": "string",
    "XML": "string",
    "NTEXT": "string",
    "SYSNAME": "string",
    "DATETIMEOFFSET": "timestamp",
    "TIME": "timestamp",
    "IMAGE": "binary",
    "ROWVERSION": "binary",
    # SQL Server TIMESTAMP is a rowversion (binary), NOT a temporal type
    "TIMESTAMP": "binary",
    # HIERARCHYID / GEOGRAPHY / GEOMETRY / SQL_VARIANT intentionally
    # unmapped: CLR/spatial columns pass through with a warning
}

_CATALOG_SQL = (
    "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, "
    "ORDINAL_POSITION FROM INFORMATION_SCHEMA.COLUMNS "
    "WHERE TABLE_SCHEMA = ? ORDER BY TABLE_NAME, ORDINAL_POSITION"
)

_ENV_VARS = (
    "SQLSERVER_HOST", "SQLSERVER_DATABASE",
    "SQLSERVER_USER", "SQLSERVER_PASSWORD",
)


def _env_connection_factory() -> Any:
    missing = [v for v in _ENV_VARS if not os.environ.get(v)]
    if missing:
        raise OSError(
            f"SQL Server connection needs env var(s) {', '.join(missing)} "
            "(see .env.example; pip install .[sqlserver] for the driver)"
        )
    import pyodbc  # optional extra: pip install .[sqlserver]

    driver = os.environ.get("SQLSERVER_DRIVER", "ODBC Driver 18 for SQL Server")
    port = os.environ.get("SQLSERVER_PORT", "1433")
    return pyodbc.connect(
        f"DRIVER={{{driver}}};"
        f"SERVER={os.environ['SQLSERVER_HOST']},{port};"
        f"DATABASE={os.environ['SQLSERVER_DATABASE']};"
        f"UID={os.environ['SQLSERVER_USER']};"
        f"PWD={os.environ['SQLSERVER_PASSWORD']};"
        "Encrypt=yes"
    )


class SqlServerBackend(SqlCatalogBackend):
    """Snapshot one SQL Server schema as one medallion layer (default Bronze)."""

    def __init__(
        self,
        source_config: dict[str, Any],
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        schema = str(source_config.get("schema", "")).strip()
        if not schema:
            raise ValueError(
                "source.schema is required for the SQL Server backend"
            )
        layer = Layer(str(source_config.get("layer", "bronze")))
        super().__init__(
            connection_factory=connection_factory or _env_connection_factory,
            catalog_query=CatalogQuery(sql=_CATALOG_SQL, params=(schema,)),
            normalizer=TypeNormalizer(SQLSERVER_TYPE_MAP, source="sqlserver"),
            layer=layer,
        )
