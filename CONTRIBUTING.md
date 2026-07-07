# Contributing

Thanks for helping make schema drift detection work everywhere. The most
valuable contribution right now is a **new source backend** — HANA,
Snowflake, Databricks, Postgres… anything that feeds a Fabric medallion
from upstream.

## Ground rules

* **TDD** — write the failing test first. The suite must be green before
  and after your change: `pytest -q`.
* **Quality gates** (all enforced by CI on every PR):

  ```bash
  pip install -e .[dev]
  pytest -q            # tests (75% coverage floor in CI)
  ruff check .         # lint
  mypy                 # types
  bandit -c pyproject.toml -r src main.py   # security
  ```

* **Conventional Commits** (`feat(...)`, `fix(...)`, `docs(...)`).
* Branch per change; PRs target `main`.
* No secrets in code or config — credentials come from `.env`
  (see `.env.example`) and are read via environment variables.

## Adding a source backend

### The seam

The drift engine never talks to a data source directly. Everything goes
through one small ABC — [`src/backends/base.py`](src/backends/base.py):

```python
class SchemaBackend(ABC):
    def list_layers(self) -> list[Layer]: ...
    def get_schema(self, layer: Layer) -> LayerSchema: ...
```

Your backend converts *your* source's catalog into `LayerSchema` /
`TableSchema` / `ColumnSchema` dataclasses. That's the whole job. The
diff engine, lineage graph, notifications, agents, and PR automation all
work unchanged on top.

### Two integration modes — pick the right one

* **(A) Direct-connect (build a backend).** Read schema straight from
  the source system. This catches drift *upstream*, before it lands in
  Fabric — the Bronze-boundary contract. A source column rename is
  caught at the door.
* **(B) Already mirrored / shortcut into Fabric (no code needed!).** If
  the source is mirrored or shortcut into a lakehouse, the existing
  `FabricBackend` already sees it. Don't build a backend for this —
  just configure live mode.

Build a backend only for mode (A).

### Recipe (most sources are <120 lines)

Nearly every warehouse/database exposes an `INFORMATION_SCHEMA` or a
system catalog. `src/backends/sql_catalog_base.py` does the heavy
lifting; a concrete backend supplies exactly three things:

1. **A connection factory** — a zero-arg callable returning a DBAPI
   connection (driver import *inside* the factory, so the driver stays
   an optional dependency).
2. **A catalog query** — returns rows of
   `(table_name, column_name, data_type, is_nullable, ordinal_position)`.
3. **A type map** — your dialect's type names → the canonical set in
   `src/backends/type_normalize.py` (`string`, `int`, `bigint`,
   `decimal`, `float`, `bool`, `timestamp`, `date`, `binary`).
   **This matters:** the drift engine compares dtype strings, so
   `NVARCHAR` vs `VARCHAR` vs `STRING` across sources would otherwise
   read as false drift.

Use [`src/backends/hana_backend.py`](src/backends/hana_backend.py) or
[`snowflake_backend.py`](src/backends/snowflake_backend.py) as the
template. Register your backend in
[`src/backends/__init__.py`](src/backends/__init__.py) (`SOURCE_BACKENDS`)
and add the driver as an optional extra in `pyproject.toml`.

### The test bar — contract suite

Every backend must pass the shared contract suite. Subclass it, provide
two fixtures, done:

```python
# tests/backends/test_mysource_backend.py
import pytest
from tests.backends.backend_contract import SchemaBackendContract

class TestMySourceContract(SchemaBackendContract):
    @pytest.fixture
    def backend(self):
        return MySourceBackend(connection_factory=fake_conn_with_data, ...)

    @pytest.fixture
    def empty_backend(self):
        return MySourceBackend(connection_factory=fake_empty_conn, ...)
```

`pytest tests/backends/ -q` tells you pass/fail without asking anyone.
The contract asserts:

* `get_all_schemas()` returns one `LayerSchema` per `list_layers()`;
* every column has `name`, `dtype`, integer `ordinal`;
* an **empty source yields an empty `LayerSchema`**, never a crash.

Use a **fake connection** (canned cursor rows) in tests — no live
credentials, no network. See
[`tests/backends/test_contract_local.py`](tests/backends/test_contract_local.py)
for the wiring pattern.

### Checklist for a backend PR

- [ ] `src/backends/<source>_backend.py` (thin: factory + query + type map)
- [ ] Driver as optional extra (`pyproject.toml` `[project.optional-dependencies]`)
- [ ] Registered in `SOURCE_BACKENDS` (`src/backends/__init__.py`)
- [ ] `tests/backends/test_<source>_backend.py` subclasses the contract suite
      + asserts your type map on representative rows
- [ ] Config/auth documented in `docs/BACKENDS.md`
- [ ] All quality gates green

## Wanted backends (claim one!)

Open a [new-backend issue](.github/ISSUE_TEMPLATE/new-backend.md) to claim.
Each is the same recipe — driver + catalog query + type map:

| Source | Driver | Catalog | Difficulty |
|---|---|---|---|
| Azure Cosmos DB | `azure-cosmos` | container document sampling (schemaless!) | **advanced** — needs a sampling strategy, not a catalog query |

Shipped so far (use these as references): HANA, Snowflake, Databricks /
Unity Catalog, Azure SQL / SQL Server, PostgreSQL, AWS Redshift,
MySQL / Aurora MySQL — see [docs/BACKENDS.md](docs/BACKENDS.md).

## Other contributions

Bug reports and drift-type proposals: open an issue with a minimal
schema-before/schema-after pair. Docs fixes: straight to PR.
