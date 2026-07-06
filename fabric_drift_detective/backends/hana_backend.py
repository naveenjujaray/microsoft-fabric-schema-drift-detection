"""SAP HANA direct-connect backend (upstream drift, mode A).

Reads column metadata from ``SYS.TABLE_COLUMNS`` so a source-side
rename/retype is caught BEFORE the nightly load lands it in Fabric.

Driver: ``hdbcli`` — optional extra (``pip install .[hana]``), imported
only inside the default connection factory.

Config (``source:`` block in config.yaml)::

    mode: source
    source:
      type: hana
      schema: "ERP"          # HANA schema to snapshot
      layer: bronze          # medallion layer it feeds (default bronze)

Credentials via .env: ``HANA_HOST``, ``HANA_PORT``, ``HANA_USER``,
``HANA_PASSWORD``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .base import Layer
from .sql_catalog_base import CatalogQuery, SqlCatalogBackend
from .type_normalize import ANSI_TYPE_MAP, TypeNormalizer

#: HANA dialect names merged over the ANSI baseline
HANA_TYPE_MAP: dict[str, str] = {
    **ANSI_TYPE_MAP,
    "SHORTTEXT": "string",
    "ALPHANUM": "string",
    "SMALLDECIMAL": "decimal",
    "SECONDDATE": "timestamp",
    "LONGDATE": "timestamp",
    "DAYDATE": "date",
    "TIME": "timestamp",
    # ST_GEOMETRY / ST_POINT intentionally unmapped: passthrough + warning
}

_CATALOG_SQL = (
    "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE_NAME, IS_NULLABLE, POSITION "
    "FROM SYS.TABLE_COLUMNS WHERE SCHEMA_NAME = ? "
    "ORDER BY TABLE_NAME, POSITION"
)

_ENV_VARS = ("HANA_HOST", "HANA_PORT", "HANA_USER", "HANA_PASSWORD")


def _env_connection_factory() -> Any:
    missing = [v for v in _ENV_VARS if not os.environ.get(v)]
    if missing:
        raise OSError(
            f"HANA connection needs env var(s) {', '.join(missing)} "
            "(see .env.example; pip install .[hana] for the hdbcli driver)"
        )
    from hdbcli import dbapi  # optional extra: pip install .[hana]

    return dbapi.connect(
        address=os.environ["HANA_HOST"],
        port=int(os.environ["HANA_PORT"]),
        user=os.environ["HANA_USER"],
        password=os.environ["HANA_PASSWORD"],
    )


class HanaBackend(SqlCatalogBackend):
    """Snapshot one HANA schema as one medallion layer (default Bronze)."""

    def __init__(
        self,
        source_config: dict[str, Any],
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        schema = str(source_config.get("schema", "")).strip()
        if not schema:
            raise ValueError("source.schema is required for the HANA backend")
        layer = Layer(str(source_config.get("layer", "bronze")))
        super().__init__(
            connection_factory=connection_factory or _env_connection_factory,
            catalog_query=CatalogQuery(sql=_CATALOG_SQL, params=(schema,)),
            normalizer=TypeNormalizer(HANA_TYPE_MAP, source="hana"),
            layer=layer,
        )
