"""AWS Redshift direct-connect backend (upstream drift, mode A).

Reads ``SVV_COLUMNS`` (covers regular AND external/Spectrum tables) so a
source-side rename/retype is caught BEFORE the nightly load lands it in
Fabric.

Driver: ``redshift_connector`` — optional extra
(``pip install .[redshift]``), imported only inside the default
connection factory.

Config (``source:`` block in config.yaml)::

    mode: source
    source:
      type: redshift
      schema: "public"       # Redshift schema to snapshot
      layer: bronze          # medallion layer it feeds (default bronze)

Credentials via .env: ``REDSHIFT_HOST``, ``REDSHIFT_DATABASE``,
``REDSHIFT_USER``, ``REDSHIFT_PASSWORD`` (optional: ``REDSHIFT_PORT``
(5439)).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .base import Layer
from .sql_catalog_base import CatalogQuery, SqlCatalogBackend
from .type_normalize import ANSI_TYPE_MAP, TypeNormalizer

#: Redshift dialect names merged over the ANSI baseline
REDSHIFT_TYPE_MAP: dict[str, str] = {
    **ANSI_TYPE_MAP,
    "TIMESTAMPTZ": "timestamp",
    "TIMETZ": "timestamp",
    "TIME": "timestamp",
    "VARBYTE": "binary",
    "BPCHAR": "string",
    # SUPER / GEOMETRY / GEOGRAPHY / HLLSKETCH intentionally unmapped:
    # semi-structured/spatial columns pass through with a warning
}

_CATALOG_SQL = (
    "SELECT table_name, column_name, data_type, is_nullable, "
    "ordinal_position FROM SVV_COLUMNS "
    "WHERE table_schema = %s ORDER BY table_name, ordinal_position"
)

_ENV_VARS = (
    "REDSHIFT_HOST", "REDSHIFT_DATABASE",
    "REDSHIFT_USER", "REDSHIFT_PASSWORD",
)


def _env_connection_factory() -> Any:
    missing = [v for v in _ENV_VARS if not os.environ.get(v)]
    if missing:
        raise OSError(
            f"Redshift connection needs env var(s) {', '.join(missing)} "
            "(see .env.example; pip install .[redshift] for the driver)"
        )
    import redshift_connector  # optional extra: pip install .[redshift]

    return redshift_connector.connect(
        host=os.environ["REDSHIFT_HOST"],
        port=int(os.environ.get("REDSHIFT_PORT") or "5439"),  # blank -> default
        database=os.environ["REDSHIFT_DATABASE"],
        user=os.environ["REDSHIFT_USER"],
        password=os.environ["REDSHIFT_PASSWORD"],
    )


class RedshiftBackend(SqlCatalogBackend):
    """Snapshot one Redshift schema as one medallion layer (default Bronze)."""

    def __init__(
        self,
        source_config: dict[str, Any],
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        schema = str(source_config.get("schema", "")).strip()
        if not schema:
            raise ValueError(
                "source.schema is required for the Redshift backend"
            )
        layer = Layer(str(source_config.get("layer", "bronze")))
        super().__init__(
            connection_factory=connection_factory or _env_connection_factory,
            catalog_query=CatalogQuery(sql=_CATALOG_SQL, params=(schema,)),
            normalizer=TypeNormalizer(REDSHIFT_TYPE_MAP, source="redshift"),
            layer=layer,
        )
