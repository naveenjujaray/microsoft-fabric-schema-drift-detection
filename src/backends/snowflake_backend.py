"""Snowflake direct-connect backend (upstream drift, mode A).

Reads ``INFORMATION_SCHEMA.COLUMNS`` so a source-side rename/retype is
caught BEFORE it reaches the Fabric mirror/shortcut.

Driver: ``snowflake-connector-python`` — optional extra
(``pip install .[snowflake]``), imported only inside the default
connection factory.

Config (``source:`` block in config.yaml)::

    mode: source
    source:
      type: snowflake
      schema: "PUBLIC"       # Snowflake schema to snapshot
      layer: bronze          # medallion layer it feeds (default bronze)

Credentials via .env: ``SNOWFLAKE_ACCOUNT``, ``SNOWFLAKE_USER``,
``SNOWFLAKE_PASSWORD``, ``SNOWFLAKE_DATABASE`` (optional:
``SNOWFLAKE_WAREHOUSE``, ``SNOWFLAKE_ROLE``).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .base import Layer
from .sql_catalog_base import CatalogQuery, SqlCatalogBackend
from .type_normalize import ANSI_TYPE_MAP, TypeNormalizer

#: Snowflake dialect names merged over the ANSI baseline
SNOWFLAKE_TYPE_MAP: dict[str, str] = {
    **ANSI_TYPE_MAP,
    "STRING": "string",
    "NUMBER": "decimal",  # Snowflake integers surface as NUMBER(38,0)
    "FLOAT4": "float",
    "FLOAT8": "float",
    "TIMESTAMP_NTZ": "timestamp",
    "TIMESTAMP_LTZ": "timestamp",
    "TIMESTAMP_TZ": "timestamp",
    "TIME": "timestamp",
    # VARIANT / OBJECT / ARRAY / GEOGRAPHY intentionally unmapped:
    # semi-structured columns pass through with a warning
}

_CATALOG_SQL = (
    "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, "
    "ORDINAL_POSITION FROM INFORMATION_SCHEMA.COLUMNS "
    "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, ORDINAL_POSITION"
)

_ENV_VARS = (
    "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER",
    "SNOWFLAKE_PASSWORD", "SNOWFLAKE_DATABASE",
)


def _env_connection_factory() -> Any:
    missing = [v for v in _ENV_VARS if not os.environ.get(v)]
    if missing:
        raise OSError(
            f"Snowflake connection needs env var(s) {', '.join(missing)} "
            "(see .env.example; pip install .[snowflake] for the driver)"
        )
    import snowflake.connector  # optional extra: pip install .[snowflake]

    kwargs: dict[str, Any] = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_USER"],
        "password": os.environ["SNOWFLAKE_PASSWORD"],
        "database": os.environ["SNOWFLAKE_DATABASE"],
    }
    for optional, key in (
        ("SNOWFLAKE_WAREHOUSE", "warehouse"),
        ("SNOWFLAKE_ROLE", "role"),
    ):
        if os.environ.get(optional):
            kwargs[key] = os.environ[optional]
    return snowflake.connector.connect(**kwargs)


class SnowflakeBackend(SqlCatalogBackend):
    """Snapshot one Snowflake schema as one medallion layer (default Bronze)."""

    def __init__(
        self,
        source_config: dict[str, Any],
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        schema = str(source_config.get("schema", "")).strip()
        if not schema:
            raise ValueError(
                "source.schema is required for the Snowflake backend"
            )
        layer = Layer(str(source_config.get("layer", "bronze")))
        super().__init__(
            connection_factory=connection_factory or _env_connection_factory,
            catalog_query=CatalogQuery(sql=_CATALOG_SQL, params=(schema,)),
            normalizer=TypeNormalizer(SNOWFLAKE_TYPE_MAP, source="snowflake"),
            layer=layer,
        )
