# Fabric setup (live mode)

Stand up the medallion workspace with the Microsoft Fabric CLI (`fab`) and wire
the IDs into `config.yaml`.

> **Verified syntax.** Commands below were checked against Microsoft Learn:
> [Fabric command line interface](https://learn.microsoft.com/rest/api/fabric/articles/fabric-command-line-interface)
> and [Create a SQL database with the Fabric CLI](https://learn.microsoft.com/fabric/database/sql/deploy-cli).
> The CLI is young and evolving (v1.x); where a flag is **not** verifiable on
> Learn it is flagged ⚠️ with a REST fallback. Never guess flags — run
> `<command> --help` inside `fab` to confirm on your version.

## 0. Prerequisites

* Python 3.10–3.12, then `pip install ms-fabric-cli` → check `fab --version`.
* A Fabric capacity (trial or F-SKU) you can assign workspaces to.
* An Azure AD **app registration** (service principal) with a client secret.
* Fabric tenant setting **"Service principals can use Fabric APIs"** enabled
  (Admin portal → Tenant settings → Developer settings).
* The SPN added to the workspace (after step 2) with **Admin** or **Member** role.

## 1. Authenticate

Interactive (any user):

```bash
fab auth login        # choose "Interactive with web browser"
```

Service principal (automation) — flags per the official
[fabric-cli reference](https://microsoft.github.io/fabric-cli/):

```bash
fab auth login -u "$AZURE_CLIENT_ID" -p "$AZURE_CLIENT_SECRET" --tenant "$AZURE_TENANT_ID"
```

⚠️ If your CLI build rejects `-u/-p/--tenant`, run `fab auth login` and pick
"Service principal authentication with secret" from the prompt (verified on
Learn), or skip the CLI entirely — `src/fabric_rest.py` talks straight to the
REST API with `ClientSecretCredential`.

## 2. Create the workspace on a capacity

```bash
fab create SchemaDriftDemo.Workspace -P "capacityName=<YOUR_CAPACITY>"
```

⚠️ The `-P capacityName=` parameter follows the fabric-cli reference; not shown
on Learn. REST fallback:

```
POST https://api.fabric.microsoft.com/v1/workspaces
{ "displayName": "SchemaDriftDemo", "capacityId": "<capacity-guid>" }
```

Then grant your SPN access: workspace → Manage access → add the app → Member.

## 3. Lakehouse (Bronze+Silver) and Warehouse (Gold)

```bash
fab create SchemaDriftDemo.Workspace/DriftLakehouse.Lakehouse -P "enableSchemas=true"
fab create SchemaDriftDemo.Workspace/DriftWarehouse.Warehouse
fab ls SchemaDriftDemo.Workspace        # verify both exist
```

The `<workspace>.Workspace/<item>.<Type>` path syntax is verified on Learn
(SQLDatabase example; Lakehouse/Warehouse are the same item-path grammar).
⚠️ `enableSchemas` parameter is from the fabric-cli reference. REST fallback:
[Create Lakehouse](https://learn.microsoft.com/rest/api/fabric/lakehouse/items/create-lakehouse) /
[Create Warehouse](https://learn.microsoft.com/rest/api/fabric/warehouse/items/create-warehouse):

```
POST /v1/workspaces/{workspaceId}/lakehouses   { "displayName": "DriftLakehouse" }
POST /v1/workspaces/{workspaceId}/warehouses   { "displayName": "DriftWarehouse" }
```

Convention used by this project: lakehouse schemas `bronze` and `silver`
(schema-enabled lakehouse), warehouse schema `dbo` for the Gold star schema.

## 4. Dataflow Gen2: AdventureWorksLT → Bronze (+ Silver/Gold transforms)

Dataflow Gen2 definitions are mashup documents — there is no one-line `fab`
command that authors the queries. Two options:

* **Portal**: Workspace → New item → Dataflow Gen2 → Get data →
  Azure SQL sample `AdventureWorksLT` → destination: DriftLakehouse `bronze`
  schema. Repeat (or use notebooks) for the Silver cleanup and Gold star-schema
  loads (the SQL lives in `sample_data/build_medallion.py` — same
  transformations, portable T-SQL/Spark SQL).
* **REST**: [Create Item](https://learn.microsoft.com/rest/api/fabric/core/items/create-item)
  with `"type": "Dataflow"` and a definition payload exported from a portal-authored
  dataflow (`fab api` can POST it: see `src/fabric_cli.py:api`).

## 5. Pipeline orchestrating bronze→silver→gold

```bash
fab create SchemaDriftDemo.Workspace/BronzeToGold.DataPipeline
```

⚠️ If item-type `DataPipeline` isn't supported by your CLI build, use REST:

```
POST /v1/workspaces/{workspaceId}/items
{ "displayName": "BronzeToGold", "type": "DataPipeline", "definition": { ... } }
```

Schedule it nightly; the drift detective runs after it (see
`.github/workflows/drift-check.yml`).

## 6. Semantic model + PBIP via Git integration

1. Build the model over the warehouse (portal: DriftWarehouse → New semantic
   model → pick Dim_/Fact_ tables; add the relationships and measures — the
   demo set is in `sample_data/build_medallion.py:write_semantic_model`).
2. Connect the workspace to Git: Workspace settings → Git integration →
   connect your repo/branch. PBIP + TMDL definitions sync into the repo folder
   you configure — point `git.reports_dir` in `config.yaml` at it.
3. The drift detective reads TMDL via REST
   `POST /v1/workspaces/{ws}/semanticModels/{id}/getDefinition?format=TMDL`
   (implemented in `src/fabric_rest.py`, LRO-aware).

## 7. Capture the IDs into config.yaml

```bash
fab get SchemaDriftDemo.Workspace -q "id"                                  # workspace_id
fab get SchemaDriftDemo.Workspace/DriftLakehouse.Lakehouse -q "id"         # lakehouse_id
fab get SchemaDriftDemo.Workspace/DriftWarehouse.Warehouse -q "id"         # warehouse_id
fab ls SchemaDriftDemo.Workspace                                           # everything else
```

⚠️ `-q` (JMESPath query on `fab get`) is from the fabric-cli reference. REST
fallback: `GET /v1/workspaces` and `GET /v1/workspaces/{id}/items` list all
IDs. Paste them into the `fabric:` block of `config.yaml`. Also fill
`sql_endpoint` / `sql_database` (Lakehouse/Warehouse settings → SQL analytics
endpoint connection string) for INFORMATION_SCHEMA-level column detail.

## 8. Microsoft Graph permissions (Teams + Outlook notifications)

Reuse the **same app registration** — do not create a second one. In Azure
portal → App registrations → your app → API permissions, add **application**
permissions:

| Permission | Used by | Verified on Learn |
|---|---|---|
| `ChannelMessage.Send` | Teams channel messages via `POST /teams/{team}/channels/{channel}/messages` | Graph channel-message docs |
| `Mail.Send` | Outlook via `POST /users/{sender}/sendMail` | [user: sendMail](https://learn.microsoft.com/graph/api/user-sendmail) — application permission `Mail.Send` |

Then **Grant admin consent**. Notes:

* `Mail.Send` (application) allows sending as *any* user — consider an
  [application access policy](https://learn.microsoft.com/graph/auth-limit-mailbox-access)
  to scope it to the alerts mailbox.
* Teams `webhook` mode (incoming webhook / Workflows URL) needs no Graph
  permissions at all — simplest start.
* If Graph mail consent is not obtainable, set `notifications.outlook.mode: smtp`
  and fill the SMTP host + `SMTP_USERNAME`/`SMTP_PASSWORD` env vars.

## 9. Final env + smoke test

`.env` (from `.env.example`): `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`,
`AZURE_CLIENT_SECRET`, `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, webhooks.

```bash
fabric-drift --mode live --baseline    # first snapshot
fabric-drift --mode live --once        # detect (after any pipeline run)
```

## 10. Run it inside Fabric instead (no secrets)

To run the detective *in the workspace itself* — notebook wrapper, scheduled
Data Factory pipeline, `fab deploy` CI/CD, and managed-identity/notebook
identity auth — see [FABRIC_NATIVE.md](FABRIC_NATIVE.md).
