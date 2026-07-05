---
name: New source backend
about: Propose or claim a new upstream source backend (HANA, Snowflake, Databricks, ...)
title: "backend: <source name>"
labels: ["backend", "enhancement"]
---

<!--
Before filing: read CONTRIBUTING.md ("Adding a source backend").
If the source is already mirrored/shortcut into a Fabric lakehouse, you
probably don't need a backend at all - the existing FabricBackend sees it.
-->

## Source system

- **Name/version:** <!-- e.g. SAP HANA Cloud, Snowflake, PostgreSQL 16 -->
- **Runs upstream of Fabric?** <!-- backends are for direct-connect upstream
  sources only (mode A). yes/no + one line on your topology -->

## Driver

- **Python driver:** <!-- e.g. hdbcli, snowflake-connector-python, psycopg -->
- **License compatible with optional-extra distribution?** <!-- yes/no -->

## Authentication

- **Method(s):** <!-- user/password, key-pair, OAuth, Kerberos, ... -->
- **Which env vars would config need?** <!-- e.g. MYSRC_HOST, MYSRC_USER,
  MYSRC_PASSWORD - secrets always via .env, never config.yaml -->

## Catalog source

- **Where does schema metadata live?** <!-- e.g. INFORMATION_SCHEMA.COLUMNS,
  SYS.TABLE_COLUMNS, SVV_COLUMNS, or "schemaless - needs sampling" -->
- **Sample query returning (table, column, dtype, nullable, ordinal):**

```sql
-- paste here
```

## Layers

- **Which medallion layer(s) does this source map to?** <!-- almost always
  BRONZE (upstream feed); explain if different -->

## Type map sketch

<!-- your dialect's types -> canonical (string/int/bigint/decimal/float/
bool/timestamp/date/binary). List the odd ones especially. -->

| Source type | Canonical |
|---|---|
|  |  |

## Claiming

- [ ] I want to implement this myself (a maintainer will assign the issue)
- [ ] I'm requesting it, can't implement
