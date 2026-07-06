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
modes — capture a baseline (`python main.py --baseline`), then run
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
