"""PostgreSQL (RDS/Aurora) direct-connect backend (upstream drift, mode A).

Reads ``information_schema.columns`` so a source-side rename/retype is
caught BEFORE the nightly load lands it in Fabric.

Driver: ``psycopg`` (v3) — optional extra (``pip install .[postgres]``),
imported only inside the default connection factory.

Config (``source:`` block in config.yaml)::

    mode: source
    source:
      type: postgres
      schema: "public"       # Postgres schema to snapshot
      layer: bronze          # medallion layer it feeds (default bronze)

Credentials via .env: ``POSTGRES_HOST``, ``POSTGRES_DATABASE``,
``POSTGRES_USER``, ``POSTGRES_PASSWORD`` (optional: ``POSTGRES_PORT``
(5432)).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .base import Layer
from .sql_catalog_base import CatalogQuery, SqlCatalogBackend
from .type_normalize import ANSI_TYPE_MAP, TypeNormalizer

#: Postgres dialect names merged over the ANSI baseline
#: (information_schema reports lowercase long-form names; the
#: normalizer uppercases before lookup)
POSTGRES_TYPE_MAP: dict[str, str] = {
    **ANSI_TYPE_MAP,
    "UUID": "string",
    "NAME": "string",
    "TIME WITHOUT TIME ZONE": "timestamp",
    "TIME WITH TIME ZONE": "timestamp",
    # JSON / JSONB / ARRAY / USER-DEFINED intentionally unmapped:
    # semi-structured columns pass through with a warning
}

_CATALOG_SQL = (
    "SELECT table_name, column_name, data_type, is_nullable, "
    "ordinal_position FROM information_schema.columns "
    "WHERE table_schema = %s ORDER BY table_name, ordinal_position"
)

_ENV_VARS = (
    "POSTGRES_HOST", "POSTGRES_DATABASE",
    "POSTGRES_USER", "POSTGRES_PASSWORD",
)


def _env_connection_factory() -> Any:
    missing = [v for v in _ENV_VARS if not os.environ.get(v)]
    if missing:
        raise OSError(
            f"Postgres connection needs env var(s) {', '.join(missing)} "
            "(see .env.example; pip install .[postgres] for the driver)"
        )
    import psycopg  # optional extra: pip install .[postgres]

    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ.get("POSTGRES_PORT") or "5432"),  # blank -> default
        dbname=os.environ["POSTGRES_DATABASE"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


class PostgresBackend(SqlCatalogBackend):
    """Snapshot one Postgres schema as one medallion layer (default Bronze)."""

    def __init__(
        self,
        source_config: dict[str, Any],
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        schema = str(source_config.get("schema", "")).strip()
        if not schema:
            raise ValueError(
                "source.schema is required for the Postgres backend"
            )
        layer = Layer(str(source_config.get("layer", "bronze")))
        super().__init__(
            connection_factory=connection_factory or _env_connection_factory,
            catalog_query=CatalogQuery(sql=_CATALOG_SQL, params=(schema,)),
            normalizer=TypeNormalizer(POSTGRES_TYPE_MAP, source="postgres"),
            layer=layer,
        )
