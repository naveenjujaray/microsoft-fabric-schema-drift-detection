<div align="center">

# 🕵️ Microsoft Fabric Schema Drift Detection

**Lineage-aware schema-drift detection for Microsoft Fabric medallion architectures —
with Claude-powered impact analysis, auto-fix PRs, and Teams / Outlook / Slack alerts.**

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-96%20passing-brightgreen.svg)](#-tests)
[![Agents](https://img.shields.io/badge/agents-10-8a2be2.svg)](#-agents--ten-tool-use-specialists)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](#-license)
[![Microsoft Fabric](https://img.shields.io/badge/Microsoft-Fabric-117865.svg)](https://learn.microsoft.com/fabric/)
[![Claude](https://img.shields.io/badge/LLM-Claude-d97757.svg)](https://docs.claude.com/)

</div>

---

> **A source column got renamed. Tonight your Silver notebook fails, tomorrow three
> Power BI dashboards silently show blanks, and nobody knows why until the CFO asks.**

This project fixes that. An autonomous agent watches every layer of a Fabric medallion
architecture, walks a **column-level lineage graph** to find what a change breaks
*three layers downstream*, asks **Claude** to judge the business impact and draft fixes,
opens a **Git PR** with the mechanical repairs, and alerts your team on
**Teams, Outlook, and Slack** — before the dashboards break.

Runs against a real Fabric tenant (**live mode**) or a local DuckDB replica of
AdventureWorksLT (**simulate mode**) with zero capacity cost — same code path.

## ⚡ Quickstart — 60 seconds, no Fabric account

```bash
pip install -r requirements.txt
bash scripts/run_demo.sh
```

Loads AdventureWorksLT → builds the medallion → snapshots baselines → injects six
kinds of drift → prints the drift report, the PR it would open, and every notification
payload. No API keys required (Claude falls back to a deterministic mock; set
`ANTHROPIC_API_KEY` for real reasoning).

## 🏛️ Architecture

```
Bronze (Lakehouse)   raw AdventureWorksLT tables, ingested as-is via Dataflow Gen2
      │  drift watch: source columns appearing/disappearing/retyped
      ▼
Silver (Lakehouse)   cleaned, deduped, conformed keys, standardized types
      │  drift watch: transformation contract changes, key/type drift
      ▼
Gold  (Warehouse)    star schema — Dim_Customer, Dim_Product, Dim_Date,
      │              Fact_Sales — consumed by the semantic model
      ▼
Semantic Model       relationships, DAX measures, RLS
      │  drift watch: a Gold column drop/rename that breaks a measure or relationship
      ▼
Power BI reports (PBIP in Git)   final consumers that break silently today
```

Detection pipeline:

```
 SchemaBackend (ABC)                 baselines (.baselines/*.json)
 ├─ FabricBackend (fab CLI + REST)          │
 └─ LocalBackend  (DuckDB + JSON)           ▼
        │ current schemas ────────►  schema_diff  ──►  lineage graph  ──►  Claude
        │                            (14 drift types)  (cross-layer     (severity,
        ▼                                               breaks)          fixes, PR text)
   five LayerSchemas                                        │
                                                            ▼
                                    git_handler (branch+PR)   notifications
                                                              (console/Slack/Teams/Outlook)
```

## 🔗 The differentiator: cross-layer lineage

`silver.customers.email` renamed to `email_address`? The lineage graph walks:

```
silver:customers.email
  └─► gold:Dim_Customer.Email
        └─► semantic_model:Customer.Email
              └─► reports:Customer Detail.Customer.Email
```

…and emits a `cross_layer_break` for each — so the alert says *"the Customer Detail
report will break"*, not just *"a column changed"*. DAX measures are parsed for
`Table[Column]` references, so `SUM(Sales[Freight])` breaks when
`silver.sales_orders.freight` is dropped, three layers up.

## 📖 Example story — the rename that would have reached the CFO

> **9:02 AM.** An upstream engineer renames a column in the CRM export:
> `email → email_address`. Nobody tells the BI team. The nightly Dataflow
> succeeds. No error anywhere.

Here is what happens **with this project running** as a scheduled Fabric pipeline:

**1 — Detect.** The 2 AM run snapshots every layer and diffs against the stored
baselines. `schema_diff` sees `email` gone and `email_address` arrived in
`silver.customers` — same type, same position, near-identical name — and
classifies it as a **`column_rename`** (not a scarier drop+add):

```
[CRITICAL] silver:customers.email column_rename ('email' -> 'email_address')
```

**2 — Walk the lineage.** The column-level graph follows the rename downstream
and synthesizes a **`cross_layer_break`** at every stop:

```
silver:customers.email
  └─► gold:Dim_Customer.Email
        └─► semantic_model:Customer.Email
              └─► reports:Customer Detail.Customer.Email   ← the dashboard the CFO opens
```

So the alert reads *"the **Customer Detail** report will break"* — not just
*"a column changed somewhere."*

**3 — Reason (Claude).** Claude judges business impact, confirms the rename is
mechanically fixable, and drafts the exact TMDL find/replace plus PR prose.
(No API key? A deterministic mock does the same shape offline.)

**4 — Fix.** `git_handler` cuts a branch `drift-fix/<timestamp>`, rewrites
`sourceColumn: email → email_address` in the PBIP TMDL, and opens a **PR** —
never touching `main`.

**5 — Alert.** One `DriftAlert` fans out to **Teams / Outlook / Slack**:
severity counts, the three worst drifts, the blast radius, and the PR link.

**6 — Gate.** The pipeline **fails the run** (`exit 1`) on critical drift, so
drift is alertable like any other pipeline failure — and CI blocks the merge
until it's resolved.

> **9:05 AM.** Instead of blank dashboards next week, the BI team has a PR
> waiting, a Teams card explaining what and why, and a red pipeline that already
> stopped the bad state from shipping.

Want to interrogate it yourself? Ask an agent:

```bash
python main.py --agent lineage_qa --task "what breaks if silver.customers.email is renamed?"
python main.py --agent triage        # rank everything currently drifting, P1→P3
```

Run the whole story locally in 60 seconds with `bash scripts/run_demo.sh` — it
injects this rename (plus a money-truncating `precision_scale_change`, a dropped
column, a type change, and more) and prints every step above.

## 🔬 Drift types

Fourteen typed drifts, grouped by the level they hit. Severity is judged by
whether a change *breaks* consumers or just *risks* them; `auto_fixable` means
the repair is mechanical (a downstream find/replace), not a business decision.

**Column-level**

| Type | Severity | Auto-fixable | Enterprise scenario |
|---|---|---|---|
| `column_drop` | 🔴 critical | ❌ | source system retires a field; everything binding to it errors |
| `column_add` | 🔵 info | ✅ | new field ingested; safe, but should flow to Silver/Gold |
| `type_change` | 🟡 warning (safe cast) / 🔴 critical | safe casts only | `INTEGER→BIGINT` widens (safe); `DECIMAL→VARCHAR` corrupts joins/measures |
| `precision_scale_change` | 🟡 warning (widen) / 🔴 critical (narrow) | widen only | `DECIMAL(19,4)→DECIMAL(10,2)` silently **truncates money**; `VARCHAR(50)→VARCHAR(20)` clips values |
| `column_rename` <sub>(heuristic: type + position + name similarity)</sub> | 🔴 critical | ✅ | a refactor renames `email→email_address`; every downstream ref breaks but is mechanically remappable |
| `column_reorder` | 🟡 warning | ✅ | positions swap; breaks `SELECT *` inserts and positional CSV/parquet binding |
| `nullability_change` | 🟡 warning | ✅ | `NOT NULL→NULL` lets nulls into a column a measure assumes is populated |

**Table-level**

| Type | Severity | Auto-fixable | Enterprise scenario |
|---|---|---|---|
| `table_drop` | 🔴 critical | ❌ | a Lakehouse table disappears; all downstream tables/reports orphaned |
| `table_add` | 🔵 info | ✅ | new entity ingested; informational |
| `key_change` | 🔴 critical | ❌ | primary/business key gained or lost; relationships and dedup logic break |

**Semantic-model-level**

| Type | Severity | Auto-fixable | Enterprise scenario |
|---|---|---|---|
| `measure_drop` | 🔴 critical | ❌ | a DAX measure is deleted; every visual bound to it goes blank |
| `measure_add` | 🔵 info | ✅ | new measure published; informational |
| `measure_change` | 🟡 warning | ❌ | measure expression edited → **numbers shift silently** under a report the business already trusts |

**Cross-layer**

| Type | Severity | Auto-fixable | Enterprise scenario |
|---|---|---|---|
| `cross_layer_break` <sub>(synthesized via lineage graph)</sub> | 🔴 critical | rename-driven only | a Bronze/Silver change reaches a Gold column, a DAX measure, or a Power BI visual **three layers up** |

Whitespace-only DAX reformatting (Power BI Desktop rewrites TMDL on every save)
is normalized away, so `measure_change` fires on real logic edits — not noise.

### Real-world drift the model is designed for

The catalog maps directly to how enterprise Fabric estates actually break:
upstream **source-system schema evolution** (SAP/Salesforce/Dynamics adding,
retyping, renaming fields), **Dataflow Gen2 / notebook refactors** silently
changing the Silver contract, **money-precision narrowing** in financial marts,
and **semantic-model edits** that move numbers without breaking a query. Drifts
that need metadata the backends don't yet capture — Delta **partition-column**
changes, **collation/case-sensitivity**, column **default** changes — are the
tracked roadmap; the engine's typed, lineage-aware design extends to them
without touching consumers.

## 🚀 Usage

```bash
python main.py --mode simulate --baseline        # capture baseline snapshots
python main.py --mode simulate --once            # one detection cycle
python main.py --mode simulate --once --dry-run  # render all payloads, send nothing
python main.py --mode live --once --open-pr      # real Fabric + real PR
python main.py --provision                       # show fab provisioning steps
```

Exit codes: `0` clean · `1` critical drift (usable as a CI gate) · `2` config error.

Everything is configured in [config.yaml](config.yaml) (IDs, model, channels) +
`.env` (secrets — see [.env.example](.env.example)). **No hardcoded IDs anywhere.**

## 🤖 Agents — ten tool-use specialists

Beyond the scheduled pipeline, ten Claude agents run **tool-use loops** over
the same backends, differ and lineage graph — for interactive investigation,
verified repair and operations:

```bash
python main.py --list-agents
python main.py --agent lineage_qa --task "what breaks if silver.sales_orders.freight is dropped?"
python main.py --agent fix_verify --allow-writes    # propose → apply → re-diff → retry
```

| Agent | Superpower |
|---|---|
| `fix_verify` ✏️ | repairs drift, then **re-runs the differ to prove the fix worked** — retries until green |
| `drift_investigator` | rename vs drop+add verdicts backed by **data profiles**, not just heuristics |
| `lineage_qa` | interactive *"what breaks if…"* / *"where does X come from"* answers |
| `root_cause` | traces symptoms upstream; groups 10 breaks under 1 root cause |
| `triage` | P1/P2/P3 fix queue ranked by blast radius + report criticality |
| `migration_planner` | step-by-step reversible migration plans (plan only — no DDL tools) |
| `pr_responder` ✏️ | reads reviewer comments on the auto-PR, adjusts edits, pushes follow-up |
| `provisioner` ✏️ | drives the `fab` CLI, captures item GUIDs into config.yaml |
| `historian` | mines archived baselines for drift trends + hotspot tables |
| `notification_composer` | engineer-Slack vs executive-email framing of the same incident |

**Production guard rails:** write tools hard-gated behind `--allow-writes`
(✏️ agents plan-only otherwise) · sandboxed file/SQL access (SELECT-only,
row-capped, `.env`/`.git` denied) · per-run turn + token budgets · JSONL
transcript of every run in `.agent_runs/` · no key? clean offline result, never
a crash. Full guide: [docs/AGENTS.md](docs/AGENTS.md).

## 🏭 Run it inside Fabric — notebook, pipeline, CI/CD

The [`fabric/`](fabric) folder ships Fabric-native artifacts (Git-integration format):

| Artifact | What it does |
|---|---|
| `DriftDetection.Notebook` | Clones the repo, runs a full cycle **in-workspace** with the notebook identity — no secrets, baselines persisted to the attached lakehouse |
| `DriftCheckPipeline.DataPipeline` | Scheduled orchestration; **fails the pipeline run** (`SchemaDriftCritical`) when critical drift is found, so drift is alertable like any pipeline failure |
| `deploy-config.yml` + `parameter.yml` | `fab deploy` (fabric-cicd) config with per-environment (DEV/TEST/PROD) parameterization |

```bash
FABRIC_WORKSPACE_ID=<guid> bash scripts/deploy_fabric.sh   # one-command deploy
```

Auth adapts automatically — four methods behind one credential
([src/azure_auth.py](src/azure_auth.py)):

| `FABRIC_AUTH_METHOD` | Identity | Where |
|---|---|---|
| `client_secret` <sub>(default outside Fabric)</sub> | SPN from `.env` | laptops, GitHub Actions |
| `notebookutils` <sub>(auto-detected in Fabric)</sub> | notebook identity | **inside Fabric notebooks** |
| `managed_identity` | system/user-assigned MI | Azure VMs, Container Apps |
| `default` | `DefaultAzureCredential` chain | everything else |

Full guide: [docs/FABRIC_NATIVE.md](docs/FABRIC_NATIVE.md).

## 📣 Notifications

One `DriftAlert` object, three formatters — content logic never duplicated:

| Channel | Format | Transport |
|---|---|---|
| **Teams** | Adaptive Card (severity counts, top 3 drifts, PR button) | incoming webhook **or** Graph `POST /teams/{id}/channels/{id}/messages` |
| **Outlook** | HTML email (severity table, per-layer breakdown, PR link) | Graph `sendMail` **or** SMTP fallback |
| **Slack** | Block Kit | incoming webhook **or** `chat.postMessage` bot |
| **Console** | rich table | always on |

Each channel independently toggleable; a failing channel never blocks the others;
`--dry-run` prints every payload. Teams/Outlook Graph calls reuse the **same**
credential as Fabric — one app registration, one auth stack (permissions:
`ChannelMessage.Send`, `Mail.Send` — see [docs/FABRIC_SETUP.md](docs/FABRIC_SETUP.md)).

## 📚 Docs

| Doc | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | module map, data flow, design decisions |
| [docs/AGENTS.md](docs/AGENTS.md) | the ten agents: tools, guard rails, examples, config |
| [docs/FABRIC_SETUP.md](docs/FABRIC_SETUP.md) | verified `fab` CLI sequence to stand up the workspace + Graph permissions |
| [docs/FABRIC_NATIVE.md](docs/FABRIC_NATIVE.md) | notebook / pipeline / `fab deploy` — running inside Fabric |
| [docs/DEMO.md](docs/DEMO.md) | the simulate-mode demo, step by step |

## ✅ Tests

```bash
pytest    # 96 tests: differ (14 drift types), lineage, auth methods, agent
          # runtime + tool guard rails (mocked), CLI wrapper (mocked),
          # Claude (mocked), notification channels, local backend
```

## 📄 License

MIT License

Copyright (c) 2026 Naveen Jujaray

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---
Made with ❤️ by Naveen Jujaray
