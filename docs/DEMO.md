# Simulate-mode demo

Runs the entire pipeline locally — DuckDB stands in for the
lakehouse/warehouse, JSON files stand in for the semantic model and PBIP
metadata. Zero Fabric capacity, zero API keys required.

## One command

```bash
pip install -e .        # installs deps + the `fabric-drift` CLI
bash scripts/run_demo.sh
```

(Windows without bash: run the five steps below directly.)

## What it does, step by step

```bash
# 1. Bronze: deterministic AdventureWorksLT subset (Customer, Product,
#    ProductCategory, SalesOrderHeader, SalesOrderDetail)
python -m sample_data.load_adventureworks

# 2. Silver (snake_case, typed, deduped) + Gold star schema
#    (Dim_Customer, Dim_Product, Dim_Date, Fact_Sales)
#    + semantic_model.json (4 tables, 6 DAX measures, relationships)
#    + reports.json (3 PBIP reports with field bindings)
python -m sample_data.build_medallion

# 3. Capture baseline schema snapshots (.baselines/*.json - one per layer)
fabric-drift --mode simulate --baseline

# 4. Break things on purpose:
#    rename       silver.customers.email -> email_address
#    drop         silver.sales_orders.freight
#    type_change  silver.sales_order_lines.unit_price DECIMAL -> VARCHAR
#    nullability  bronze.Product.StandardCost NOT NULL -> nullable
#    add          bronze.Customer.LoyaltyTier (harmless, info-level)
python -m sample_data.inject_drift --scenario all

# 5. Detect + reason + report (dry-run: renders PR body and every
#    notification payload to the console, sends nothing)
fabric-drift --mode simulate --once --dry-run
```

## What you should see

* **Cross-workspace banner** first: the shipped
  [workspace manifest](../sample_data/workspaces.json) maps the demo estate
  onto three workspaces (Contoso-Ingestion → Contoso-Enterprise-DW →
  Contoso-Reporting), so the run opens with
  `Cross-workspace impact: N break(s) reaching workspace(s): ...`.
* **Drift table** (rich console): ~15 records — the injected drifts plus the
  synthesized breaks, e.g. the freight drop cascading to
  `gold:Fact_Sales.Freight` → `semantic_model:Sales.Freight` → measure
  `Revenue incl Freight` → report `Customer Detail`. Because those targets
  live in *other* workspaces, they surface as `cross_workspace_break`
  (annotated `via onelake_shortcut` etc.); remove
  `lineage.workspaces_manifest` from `config.yaml` to see plain
  `cross_layer_break`s instead.
* **PR preview**: branch name, conventional-commit subject, and a PR body with
  "Drift detected / Fixes applied / Needs human review" sections. The rename is
  auto-fixable (`sourceColumn: email` → `sourceColumn: email_address` in
  `pbip_reports/definition/tables/Dim_Customer.tmdl`); the drop and unsafe type
  change are flagged for humans.
* **Notification payload panels** for every enabled channel. Enable the real
  ones in `config.yaml` (`notifications.slack.enabled: true` + webhook in
  `.env`) and re-run without `--dry-run` to actually send.

## Variations

```bash
python -m sample_data.inject_drift --scenario rename   # a single scenario
fabric-drift --mode simulate --once                  # send real notifications
ANTHROPIC_API_KEY=sk-... fabric-drift --mode simulate --once --dry-run
                                                       # real Claude reasoning
pytest                                                 # the full test suite
```

To reset: `bash scripts/run_demo.sh` wipes and rebuilds everything.
