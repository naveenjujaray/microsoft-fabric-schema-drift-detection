"""Azure Cosmos DB direct-connect backend (upstream drift, mode A).

Cosmos is schemaless — there is no catalog to query. The schema is
INFERRED by sampling documents per container: field names become
columns, JSON value types map to canonical dtypes, a field that is null
or missing in any sampled document is nullable. Drift then means the
*shape of the data* changed — a field disappeared, changed type, went
nullable — which is exactly the contract downstream Fabric ingestion
relies on.

Deterministic by construction: fields are ordered alphabetically
(ordinal = sort index) and conflicting value types collapse to
``mixed``, so two runs over the same data always produce the same
snapshot.

Driver: ``azure-cosmos`` — optional extra (``pip install .[cosmos]``),
imported only inside the default client factory.

Config (``source:`` block in config.yaml)::

    mode: source
    source:
      type: cosmos
      database: "shop"       # Cosmos database to snapshot
      containers: []         # optional explicit list (default: all)
      sample_size: 100       # documents sampled per container
      layer: bronze          # medallion layer it feeds (default bronze)

Credentials via .env: ``COSMOS_ENDPOINT``, ``COSMOS_KEY``.

ponytail: sampling ceiling — a field rarer than 1/sample_size can flap
between column_add/column_drop across runs; raise source.sample_size if
your documents are heterogeneous.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from itertools import islice
from typing import Any

from .base import ColumnSchema, Layer, LayerSchema, SchemaBackend, TableSchema

_ENV_VARS = ("COSMOS_ENDPOINT", "COSMOS_KEY")

#: JSON scalar -> canonical dtype; bool MUST precede int (bool is an int
#: subclass in Python)
_SCALARS: tuple[tuple[type, str], ...] = (
    (bool, "bool"),
    (int, "int"),
    (float, "float"),
    (str, "string"),
)


def _dtype_of(value: Any) -> str:
    for typ, name in _SCALARS:
        if isinstance(value, typ):
            return name
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "string"  # anything exotic round-trips as its string form


def _env_client_factory() -> Any:
    missing = [v for v in _ENV_VARS if not os.environ.get(v)]
    if missing:
        raise OSError(
            f"Cosmos DB connection needs env var(s) {', '.join(missing)} "
            "(see .env.example; pip install .[cosmos] for the driver)"
        )
    from azure.cosmos import CosmosClient  # optional extra: pip install .[cosmos]

    return CosmosClient(
        os.environ["COSMOS_ENDPOINT"], credential=os.environ["COSMOS_KEY"]
    )


class CosmosBackend(SchemaBackend):
    """Snapshot one Cosmos database as one medallion layer via sampling."""

    def __init__(
        self,
        source_config: dict[str, Any],
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        database = str(source_config.get("database", "")).strip()
        if not database:
            raise ValueError(
                "source.database is required for the Cosmos DB backend"
            )
        self.database = database
        self.containers = [str(c) for c in source_config.get("containers") or []]
        self.sample_size = max(1, int(source_config.get("sample_size", 100)))
        self.layer = Layer(str(source_config.get("layer", "bronze")))
        self._client_factory = client_factory or _env_client_factory

    # ------------------------------------------------------------------
    def list_layers(self) -> list[Layer]:
        return [self.layer]

    def get_schema(self, layer: Layer) -> LayerSchema:
        if layer is not self.layer:
            raise ValueError(
                f"CosmosBackend serves layer {self.layer.value!r}, "
                f"not {layer.value!r}"
            )
        client = self._client_factory()
        db = client.get_database_client(self.database)
        names = self.containers or sorted(
            str(c["id"]) for c in db.list_containers()
        )
        result = LayerSchema(layer=self.layer)
        for name in names:
            docs = list(islice(
                db.get_container_client(name).query_items(
                    query="SELECT * FROM c",
                    enable_cross_partition_query=True,
                ),
                self.sample_size,
            ))
            result.tables[name] = self._infer_table(name, docs)
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _infer_table(name: str, docs: list[dict[str, Any]]) -> TableSchema:
        types: dict[str, set[str]] = {}
        null_seen: dict[str, bool] = {}
        presence: dict[str, int] = {}
        for doc in docs:
            for field, value in doc.items():
                if field.startswith("_"):
                    continue  # Cosmos system properties (_rid, _ts, ...)
                presence[field] = presence.get(field, 0) + 1
                if value is None:
                    null_seen[field] = True
                    continue
                types.setdefault(field, set()).add(_dtype_of(value))

        table = TableSchema(name=name)
        for ordinal, field in enumerate(sorted(presence)):
            seen = types.get(field, set())
            dtype = seen.pop() if len(seen) == 1 else ("mixed" if seen else "string")
            table.columns[field] = ColumnSchema(
                name=field,
                dtype=dtype,
                nullable=null_seen.get(field, False)
                or presence[field] < len(docs),
                ordinal=ordinal,
            )
        return table
