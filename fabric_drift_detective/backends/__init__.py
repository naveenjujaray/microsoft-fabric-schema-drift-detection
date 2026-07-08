"""Schema backends: live Fabric, local simulation, or direct-connect sources.

Direct-connect (upstream) source backends register here by config
``type``. ``make_source_backend`` is the single factory the CLI uses
for ``mode: source``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import SchemaBackend


def _hana(cfg: dict[str, Any], factory: Callable[[], Any] | None) -> SchemaBackend:
    from .hana_backend import HanaBackend

    return HanaBackend(cfg, connection_factory=factory)


def _snowflake(
    cfg: dict[str, Any], factory: Callable[[], Any] | None
) -> SchemaBackend:
    from .snowflake_backend import SnowflakeBackend

    return SnowflakeBackend(cfg, connection_factory=factory)


def _databricks(
    cfg: dict[str, Any], factory: Callable[[], Any] | None
) -> SchemaBackend:
    from .databricks_backend import DatabricksBackend

    return DatabricksBackend(cfg, connection_factory=factory)


def _sqlserver(
    cfg: dict[str, Any], factory: Callable[[], Any] | None
) -> SchemaBackend:
    from .sqlserver_backend import SqlServerBackend

    return SqlServerBackend(cfg, connection_factory=factory)


def _postgres(
    cfg: dict[str, Any], factory: Callable[[], Any] | None
) -> SchemaBackend:
    from .postgres_backend import PostgresBackend

    return PostgresBackend(cfg, connection_factory=factory)


def _redshift(
    cfg: dict[str, Any], factory: Callable[[], Any] | None
) -> SchemaBackend:
    from .redshift_backend import RedshiftBackend

    return RedshiftBackend(cfg, connection_factory=factory)


def _mysql(cfg: dict[str, Any], factory: Callable[[], Any] | None) -> SchemaBackend:
    from .mysql_backend import MySqlBackend

    return MySqlBackend(cfg, connection_factory=factory)


def _cosmos(cfg: dict[str, Any], factory: Callable[[], Any] | None) -> SchemaBackend:
    from .cosmos_backend import CosmosBackend

    # Cosmos has no DBAPI connection; the factory yields a CosmosClient
    return CosmosBackend(cfg, client_factory=factory)


#: config source.type -> backend builder (lazy imports keep optional
#: drivers optional). Contributors: add your backend here.
SOURCE_BACKENDS: dict[
    str, Callable[[dict[str, Any], Callable[[], Any] | None], SchemaBackend]
] = {
    "hana": _hana,
    "snowflake": _snowflake,
    "databricks": _databricks,
    "sqlserver": _sqlserver,
    "postgres": _postgres,
    "redshift": _redshift,
    "mysql": _mysql,
    "cosmos": _cosmos,
}


def make_source_backend(
    source_config: dict[str, Any],
    connection_factory: Callable[[], Any] | None = None,
) -> SchemaBackend:
    """Build a direct-connect backend from the ``source:`` config block."""
    source_type = str(source_config.get("type", "")).strip().lower()
    if not source_type:
        raise ValueError(
            "source.type missing in config.yaml - set one of "
            f"{sorted(SOURCE_BACKENDS)}"
        )
    builder = SOURCE_BACKENDS.get(source_type)
    if builder is None:
        raise ValueError(
            f"unknown source type {source_type!r}; available: "
            f"{sorted(SOURCE_BACKENDS)} (see CONTRIBUTING.md to add one)"
        )
    return builder(source_config, connection_factory)
