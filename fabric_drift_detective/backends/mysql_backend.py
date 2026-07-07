"""MySQL / Aurora MySQL direct-connect backend (upstream drift, mode A).

Reads ``INFORMATION_SCHEMA.COLUMNS`` so a source-side rename/retype is
caught BEFORE the nightly load lands it in Fabric. In MySQL a "schema"
IS a database — ``source.schema`` names the database to snapshot.

Driver: ``mysql-connector-python`` — optional extra
(``pip install .[mysql]``), imported only inside the default connection
factory.

Config (``source:`` block in config.yaml)::

    mode: source
    source:
      type: mysql
      schema: "shop"         # MySQL database to snapshot
      layer: bronze          # medallion layer it feeds (default bronze)

Credentials via .env: ``MYSQL_HOST``, ``MYSQL_USER``, ``MYSQL_PASSWORD``
(optional: ``MYSQL_PORT`` (3306)).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .base import Layer
from .sql_catalog_base import CatalogQuery, SqlCatalogBackend
from .type_normalize import ANSI_TYPE_MAP, TypeNormalizer

#: MySQL dialect names merged over the ANSI baseline
MYSQL_TYPE_MAP: dict[str, str] = {
    **ANSI_TYPE_MAP,
    "MEDIUMINT": "int",
    "YEAR": "int",
    "TINYTEXT": "string",
    "MEDIUMTEXT": "string",
    "LONGTEXT": "string",
    "ENUM": "string",
    "SET": "string",
    "TINYBLOB": "binary",
    "MEDIUMBLOB": "binary",
    "LONGBLOB": "binary",
    "TIME": "timestamp",
    # JSON / GEOMETRY / POINT etc. intentionally unmapped:
    # semi-structured/spatial columns pass through with a warning
}

_CATALOG_SQL = (
    "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, "
    "ORDINAL_POSITION FROM INFORMATION_SCHEMA.COLUMNS "
    "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, ORDINAL_POSITION"
)

_ENV_VARS = ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD")


def _env_connection_factory() -> Any:
    missing = [v for v in _ENV_VARS if not os.environ.get(v)]
    if missing:
        raise OSError(
            f"MySQL connection needs env var(s) {', '.join(missing)} "
            "(see .env.example; pip install .[mysql] for the driver)"
        )
    import mysql.connector  # optional extra: pip install .[mysql]

    return mysql.connector.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )


class MySqlBackend(SqlCatalogBackend):
    """Snapshot one MySQL database as one medallion layer (default Bronze)."""

    def __init__(
        self,
        source_config: dict[str, Any],
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        schema = str(source_config.get("schema", "")).strip()
        if not schema:
            raise ValueError(
                "source.schema (the MySQL database) is required for the "
                "MySQL backend"
            )
        layer = Layer(str(source_config.get("layer", "bronze")))
        super().__init__(
            connection_factory=connection_factory or _env_connection_factory,
            catalog_query=CatalogQuery(sql=_CATALOG_SQL, params=(schema,)),
            normalizer=TypeNormalizer(MYSQL_TYPE_MAP, source="mysql"),
            layer=layer,
        )
