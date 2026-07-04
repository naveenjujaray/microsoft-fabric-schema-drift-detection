<div align="center">

# 🕵️ Microsoft Fabric Schema Drift Detection

**Lineage-aware schema-drift detection for Microsoft Fabric medallion architectures —
with Claude-powered impact analysis, auto-fix PRs, and Teams / Outlook / Slack alerts.**

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-64%20passing-brightgreen.svg)](#-tests)
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

Loads AdventureWorksLT → builds the medallion → snapshots baselines → injects five
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
        │                            (8 drift types)   (cross-layer     (severity,
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

## 🔬 Drift types

| Type | Severity | Auto-fixable |
|---|---|---|
| `column_drop` | 🔴 critical | ❌ |
| `column_add` | 🔵 info | ✅ |
| `type_change` | 🟡 warning (safe cast) / 🔴 critical | safe casts only |
| `column_rename` <sub>(heuristic: type + position + name similarity)</sub> | 🔴 critical | ✅ |
| `nullability_change` | 🟡 warning | ✅ |
| `table_drop` / `table_add` | 🔴 critical / 🔵 info | ❌ / ✅ |
| `key_change` | 🔴 critical | ❌ |
| `cross_layer_break` <sub>(via lineage graph)</sub> | 🔴 critical | rename-driven only |

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
| [docs/FABRIC_SETUP.md](docs/FABRIC_SETUP.md) | verified `fab` CLI sequence to stand up the workspace + Graph permissions |
| [docs/FABRIC_NATIVE.md](docs/FABRIC_NATIVE.md) | notebook / pipeline / `fab deploy` — running inside Fabric |
| [docs/DEMO.md](docs/DEMO.md) | the simulate-mode demo, step by step |

## ✅ Tests

```bash
pytest    # 64 tests: differ, lineage, auth methods, CLI wrapper (mocked),
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
