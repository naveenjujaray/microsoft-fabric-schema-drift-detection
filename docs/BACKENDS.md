# Source backends

The drift engine reads schemas through one seam — `SchemaBackend`
([src/backends/base.py](../src/backends/base.py)). This doc covers every
way to point it at a data source.

## Two integration modes

**Mode B — already in Fabric (no backend needed).** If your source is
mirrored or shortcut into a lakehouse, the existing `FabricBackend`
already sees it: use `mode: live`. Don't build or configure a source
backend for this.

**Mode A — direct-connect (this doc).** Read schema straight from the
source system, *upstream* of Fabric. This catches drift at the Bronze
door: a source column rename is detected before the nightly load ever
lands it. Configure `mode: source` plus a `source:` block.

```yaml
mode: source
source:
  type: snowflake        # which backend
  schema: "PUBLIC"       # source schema to snapshot
  layer: bronze          # medallion layer it feeds (default bronze)
```

Baselines/diff/lineage/alerting all behave exactly as in the other
modes — capture a baseline (`fabric-drift --baseline`), then run
detection (`--once`).

## Shipped backends

### SAP HANA

| | |
|---|---|
| Driver | `hdbcli` — `pip install "fabric-schema-drift-detective[hana]"` |
| Catalog | `SYS.TABLE_COLUMNS` (schema-filtered, bind param) |
| Auth (.env) | `HANA_HOST`, `HANA_PORT`, `HANA_USER`, `HANA_PASSWORD` |
| Config | `source.type: hana`, `source.schema`, `source.layer` |

Type notes: HANA-only temporals map cleanly (`SECONDDATE`/`LONGDATE` →
`timestamp`, `DAYDATE` → `date`); `SHORTTEXT`/`ALPHANUM` → `string`;
`SMALLDECIMAL` → `decimal`. Spatial types (`ST_GEOMETRY`, `ST_POINT`)
are deliberately unmapped — they pass through with a one-time warning.

### Snowflake

| | |
|---|---|
| Driver | `snowflake-connector-python` — `pip install "fabric-schema-drift-detective[snowflake]"` |
| Catalog | `INFORMATION_SCHEMA.COLUMNS` (schema-filtered, bind param) |
| Auth (.env) | `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD`, `SNOWFLAKE_DATABASE` (+ optional `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_ROLE`) |
| Config | `source.type: snowflake`, `source.schema`, `source.layer` |

Type notes: every Snowflake integer surfaces as `NUMBER(38,0)` — the
map sends `NUMBER` → `decimal` (parameters preserved), so
`NUMBER(38,0)` vs `NUMBER(19,4)` still trips `precision_scale_change`.
All three timestamp flavors (`_NTZ`/`_LTZ`/`_TZ`) → `timestamp`.
Semi-structured (`VARIANT`/`OBJECT`/`ARRAY`) is unmapped by design —
schemaless columns pass through with a warning.

### Databricks / Unity Catalog

| | |
|---|---|
| Driver | `databricks-sql-connector` — `pip install "fabric-schema-drift-detective[databricks]"` |
| Catalog | `system.information_schema.columns` (catalog + schema filtered, bind params) |
| Auth (.env) | `DATABRICKS_SERVER_HOSTNAME`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN` |
| Config | `source.type: databricks`, `source.catalog`, `source.schema`, `source.layer` |

Type notes: this is the only backend needing **two** config identifiers —
Unity Catalog is three-level (`catalog.schema.table`). `STRING` →
`string`, `TIMESTAMP_NTZ` → `timestamp`, `LONG` → `bigint`. Complex
types (`ARRAY`/`MAP`/`STRUCT`/`VARIANT`/`INTERVAL`) are unmapped by
design — they pass through with a warning.

Connect to a **SQL warehouse** HTTP path (or a cluster on DBR 14.2+):
the catalog query uses native bind parameters, which older classic
clusters don't support server-side.

### Azure SQL / SQL Server

| | |
|---|---|
| Driver | `pyodbc` — `pip install "fabric-schema-drift-detective[sqlserver]"` + a Microsoft ODBC driver on the host |
| Catalog | `INFORMATION_SCHEMA.COLUMNS` (schema-filtered, bind param) |
| Auth (.env) | `SQLSERVER_HOST`, `SQLSERVER_DATABASE`, `SQLSERVER_USER`, `SQLSERVER_PASSWORD` (+ optional `SQLSERVER_PORT` (1433), `SQLSERVER_DRIVER` (default "ODBC Driver 18 for SQL Server"), `SQLSERVER_TRUST_CERT=yes` for on-prem self-signed certs) |
| Config | `source.type: sqlserver`, `source.schema`, `source.layer` |

Type notes: **`TIMESTAMP` maps to `binary`** — in SQL Server it is a
rowversion, not a temporal type. `UNIQUEIDENTIFIER`/`XML`/`NTEXT` →
`string`, `DATETIMEOFFSET` → `timestamp`, `IMAGE`/`ROWVERSION` →
`binary`. CLR/spatial types (`HIERARCHYID`, `GEOGRAPHY`, `GEOMETRY`,
`SQL_VARIANT`) are unmapped — passthrough with a warning.

### PostgreSQL (RDS / Aurora)

| | |
|---|---|
| Driver | `psycopg` v3 — `pip install "fabric-schema-drift-detective[postgres]"` |
| Catalog | `information_schema.columns` (schema-filtered, bind param) |
| Auth (.env) | `POSTGRES_HOST`, `POSTGRES_DATABASE`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (+ optional `POSTGRES_PORT` (5432)) |
| Config | `source.type: postgres`, `source.schema`, `source.layer` |

Type notes: Postgres reports long-form lowercase names — `character
varying`, `timestamp without time zone`, `double precision` — all
covered by the ANSI baseline (lookup is case-insensitive). `UUID` →
`string`; both `TIME` flavors → `timestamp`. `JSON`/`JSONB`/`ARRAY`/
`USER-DEFINED` (enums) are unmapped — passthrough with a warning.

### AWS Redshift

| | |
|---|---|
| Driver | `redshift_connector` — `pip install "fabric-schema-drift-detective[redshift]"` |
| Catalog | `SVV_COLUMNS` (covers regular **and** external/Spectrum tables; schema-filtered, bind param) |
| Auth (.env) | `REDSHIFT_HOST`, `REDSHIFT_DATABASE`, `REDSHIFT_USER`, `REDSHIFT_PASSWORD` (+ optional `REDSHIFT_PORT` (5439)) |
| Config | `source.type: redshift`, `source.schema`, `source.layer` |

Type notes: Postgres-style long-form names plus Redshift shorthands —
`TIMESTAMPTZ`/`TIMETZ`/`TIME` → `timestamp`, `VARBYTE` → `binary`,
`BPCHAR` → `string`. `SUPER`/`GEOMETRY`/`GEOGRAPHY`/`HLLSKETCH` are
unmapped — passthrough with a warning.

### MySQL / Aurora MySQL

| | |
|---|---|
| Driver | `mysql-connector-python` — `pip install "fabric-schema-drift-detective[mysql]"` |
| Catalog | `INFORMATION_SCHEMA.COLUMNS` (schema-filtered, bind param) |
| Auth (.env) | `MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASSWORD` (+ optional `MYSQL_PORT` (3306)) |
| Config | `source.type: mysql`, `source.schema` (the **database** — in MySQL schema = database), `source.layer` |

Type notes: the text/blob size ladder collapses (`TINYTEXT`/
`MEDIUMTEXT`/`LONGTEXT` → `string`, `TINYBLOB`/`MEDIUMBLOB`/`LONGBLOB`
→ `binary`); `ENUM`/`SET` → `string`, `MEDIUMINT`/`YEAR` → `int`.
`JSON` and spatial types are unmapped — passthrough with a warning.

### Azure Cosmos DB (schemaless — inferred by sampling)

| | |
|---|---|
| Driver | `azure-cosmos` — `pip install "fabric-schema-drift-detective[cosmos]"` |
| Catalog | **none** — schema inferred by sampling documents per container |
| Auth (.env) | `COSMOS_ENDPOINT`, `COSMOS_KEY` |
| Config | `source.type: cosmos`, `source.database`, `source.layer` (+ optional `source.containers` list (default: all), `source.sample_size` (default 100)) |

Cosmos has no catalog to query, so this backend samples up to
`sample_size` documents per container and infers the contract: field
names become columns, JSON value types map to `bool`/`int`/`float`/
`string`/`object`/`array`, a field null or missing in any sampled
document is nullable, and a field with conflicting types across
documents becomes `mixed` (except `int`+`float`, which fold to `float`
— JSON writes `2.0` as `2`). A field that is null in *every* sampled
document is skipped, not guessed: its type is unknowable, and guessing
would fire a false critical `type_change` when values arrive. Ordering
is alphabetical and inference is deterministic — the same data always
snapshots identically. Exactly the Cosmos system properties (`_rid`,
`_self`, `_etag`, `_attachments`, `_ts`) are stripped; user-defined
underscore fields are watched like any other.

**Sampling ceiling:** a field rarer than 1/`sample_size` can flap
between `column_add`/`column_drop` across runs — raise
`source.sample_size` for heterogeneous containers.

## Default & flag capture (default_change / flag_change)

The differ fires `default_change` when a column's default expression
changes and `flag_change` when source-declared attributes (`identity`,
`computed`, `auto_increment`, …) change. Both are **opt-in per
backend**: a backend that doesn't capture them leaves
`ColumnSchema.default`/`flags` empty and the checks stay silent.

* The **simulate backend** captures defaults from DuckDB.
* **SQL-catalog backends** opt in by widening their catalog query to a
  6th column (default expression) and optionally a 7th
  (comma-separated flags) — `SqlCatalogBackend` picks them up
  automatically, no code change needed.

## Fabric-native (mode: live)

| Layer | Item | Config |
|---|---|---|
| Bronze / Silver | Lakehouse | `fabric.lakehouse_id` (+ optional `sql_endpoint`) |
| Gold | Warehouse **or** Lakehouse | `fabric.warehouse_id`, or `fabric.gold_source: lakehouse` |
| Semantic model | SemanticModel | `fabric.semantic_model_id` (TMDL via getDefinition) |
| Reports | PBIP in Git | `git.reports_dir` |

`fabric.gold_source: lakehouse` reads the Gold star schema from the
lakehouse SQL analytics endpoint (`gold` schema, or `gold_` table
prefix on the REST fallback) instead of a warehouse's `dbo`.

## Type normalization — why it matters

The drift engine compares `dtype` strings. `NVARCHAR` (HANA) vs
`STRING` (Snowflake) vs `VARCHAR` (Postgres) would read as
`type_change` drift across sources. Every backend therefore normalizes
its dialect to one canonical vocabulary before schemas reach the diff:

```
string · int · bigint · decimal · float · bool · timestamp · date · binary
```

Parameters are preserved (`NVARCHAR(50)` → `string(50)`) because
precision/scale/length feed `precision_scale_change` detection.
Unmapped types pass through unchanged with a one-time warning — never a
crash, and same-source diffs stay correct even with an incomplete map.
See [src/backends/type_normalize.py](../src/backends/type_normalize.py).

## Adding a new backend

Full guide: [CONTRIBUTING.md](../CONTRIBUTING.md). Short version — a
backend is three things handed to `SqlCatalogBackend`:

1. a **connection factory** (driver import inside it → optional dep),
2. a **catalog query** returning
   `(table, column, dtype, nullable, ordinal)` rows,
3. a **type map** into the canonical vocabulary.

Register it in `SOURCE_BACKENDS`
([src/backends/\_\_init\_\_.py](../src/backends/__init__.py)), pass the
shared contract suite
([tests/backends/backend_contract.py](../tests/backends/backend_contract.py)),
add the driver as an optional extra. The wanted-backends table in
CONTRIBUTING lists claimable sources with their drivers and catalog
queries.
