# Cross-workspace lineage

Enterprise Fabric estates almost never live in one workspace. A common
topology separates ingestion, warehousing and reporting:

```
┌─────────────────────────┐      ┌──────────────────────────────┐      ┌───────────────────────┐
│ Workspace A             │      │ Workspace B                  │      │ Workspace C           │
│ Contoso-Ingestion       │      │ Contoso-Enterprise-DW        │      │ Contoso-Reporting     │
│                         │      │                              │      │                       │
│  Lakehouse              │ One- │  Warehouse (Gold)            │ sem. │  Power BI reports     │
│   ├─ Bronze             │ Lake │   └─ star schema             │model │   ├─ Customer Detail  │
│   └─ Silver ────────────┼──────┼─► Semantic Model ────────────┼──────┼─► ├─ Exec Summary     │
│                         │ short│   └─ DAX measures            │ bind │   └─ Product Pricing  │
└─────────────────────────┘ cut  └──────────────────────────────┘      └───────────────────────┘
```

A column dropped in Workspace A's Silver lakehouse breaks the Gold star
schema in Workspace B *through the shortcut*, the semantic model on top
of it, and the reports in Workspace C — three workspaces, one drift.
The lineage engine understands this and reports it as
**`cross_workspace_break`** drift events.

## The workspace manifest

Cross-workspace awareness is driven by a JSON manifest
(`lineage.workspaces_manifest` in `config.yaml`; the shipped sample is
[sample_data/workspaces.json](../sample_data/workspaces.json)):

```json
{
  "tenant_id": "11111111-....",
  "workspaces": [
    {
      "workspace_id": "a1000000-....",
      "name": "Contoso-Ingestion",
      "items": [
        {"item_id": "a1100000-....", "type": "Lakehouse",
         "name": "IngestionLakehouse", "layers": ["bronze", "silver"]}
      ]
    }
  ],
  "links": [
    {"type": "onelake_shortcut",
     "from": {"workspace": "Contoso-Ingestion", "layer": "silver"},
     "to":   {"workspace": "Contoso-Enterprise-DW", "layer": "gold"}}
  ]
}
```

* **workspaces** — each with `workspace_id`, `name`, optional
  `tenant_id` (defaults to the top-level tenant), and the Fabric
  **items** it hosts. Every item declares which medallion layers it
  carries, its `item_id` and artifact type (`Lakehouse`, `Warehouse`,
  `SemanticModel`, `Report`, `MirroredDatabase`, ...).
* **links** — typed cross-workspace connections. Supported types:
  `shortcut`, `onelake_shortcut`, `lakehouse`, `warehouse`,
  `semantic_model`, `mirrored_database`, `cross_reference`.

No manifest configured (or file absent) → single-workspace mode:
behavior is exactly as before, everything cross-layer is a
`cross_layer_break`.

## What changes when a manifest is present

1. Every source drift is stamped with its owning **workspace** name
   (visible in JSON payloads, PR bodies and notifications).
2. Impacted downstream nodes in a **different workspace** produce
   `cross_workspace_break` records (critical) instead of plain
   `cross_layer_break`, annotated with:
   * the source node and its workspace (`old`),
   * the breaking drift type and the **link type** it traveled through
     (`new`, e.g. `broken by column_drop via onelake_shortcut`),
   * a `crosses tenant boundary` note when the two workspaces belong to
     different tenants.
3. Impact analysis (Claude or the deterministic mock) includes for each
   drift: the **workspace name**, the **artifact name** and
   **workspace path** (`Contoso-Enterprise-DW / EnterpriseWarehouse
   (Warehouse) / Fact_Sales.Freight`), and the **blast radius per
   workspace**. When more than one workspace is impacted the summary
   states: *"This schema change impacts assets across multiple
   Microsoft Fabric workspaces."*
4. The CLI prints a cross-workspace banner:

   ```
   Cross-workspace impact: 11 break(s) reaching workspace(s):
   Contoso-Enterprise-DW, Contoso-Reporting
   ```

5. Agents get a `workspace_map` tool: the topology, plus per-node
   workspace path and cross-workspace blast radius. `lineage_qa` and
   `triage` use it to rank cross-workspace breakage above
   same-workspace noise.

## Simulate mode

The shipped manifest maps the demo estate onto the three-workspace
topology above, so `bash scripts/run_demo.sh` produces
`cross_workspace_break` records out of the box. Delete or empty
`lineage.workspaces_manifest` in `config.yaml` to demo single-workspace
behavior.

## Live mode

Point `lineage.workspaces_manifest` at a manifest describing your real
estate — workspace/item GUIDs come from `fab ls` or the portal URL. The
manifest is deliberately declarative: shortcut discovery via the Fabric
REST API (`GET /workspaces/{id}/items/{id}/shortcuts`) can generate it,
but reviewing the topology by hand keeps the lineage engine's
assumptions explicit and auditable.

## Design notes

* Layer → workspace assignment is per-item: one workspace owns each
  medallion layer. Splitting one layer across workspaces is not
  supported (and unusual in practice — shortcuts exist precisely so a
  layer can be *consumed* elsewhere without being *owned* there).
* `cross_workspace_break` replaces (not duplicates) the
  `cross_layer_break` that the same target would otherwise produce, so
  alert counts stay stable when a manifest is added.
* Tenant boundaries are informational: cross-tenant links are legal in
  Fabric (B2B shortcuts) but worth flagging loudly in impact analysis.
