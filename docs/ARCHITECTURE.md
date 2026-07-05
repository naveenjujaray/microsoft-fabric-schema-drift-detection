# Architecture

## Design goals

1. **One code path, two worlds.** The drift engine never knows whether schemas
   came from a live Fabric tenant or a local DuckDB file. `SchemaBackend` (ABC)
   is the seam; `FabricBackend` and `LocalBackend` are the implementations.
2. **Lineage-aware, not table-diffing.** A rename in Silver must name the Gold
   columns, DAX measures and PBIP reports it breaks. The lineage graph is the
   core data structure, not an afterthought.
3. **LLM optional.** Claude improves severity judgment, fix quality and PR
   prose — but `MockReasoner` keeps the pipeline fully functional (and testable,
   and demoable) without a key.
4. **No second auth stack.** One Azure AD app registration serves Fabric REST,
   Teams Graph and Outlook Graph via a single cached `ClientSecretCredential`
   (`src/azure_auth.py`).

## Module map

```
main.py                     CLI + orchestration of one detection cycle
src/
  config.py                 config.yaml + .env loading, ${VAR} interpolation
  azure_auth.py             the ONE ClientSecretCredential (Fabric + Graph scopes)
  backends/
    base.py                 Layer enum, ColumnSchema/TableSchema/LayerSchema,
                            SchemaBackend ABC
    local_backend.py        DuckDB information_schema + JSON model/report metadata
    fabric_backend.py       live: REST lakehouse tables, SQL endpoint
                            INFORMATION_SCHEMA, TMDL parsing, PBIP scanning
  fabric_cli.py             `fab` wrapper (single mockable run() choke point)
  fabric_rest.py            Fabric REST: items, lakehouse tables, semantic-model
                            getDefinition (LRO polling), SQL endpoint via pyodbc;
                            retries with exponential backoff + jitter on
                            429/408/5xx and connection failures
  medallion.py              Bronze->Silver->Gold column mappings (single source
                            of truth for transforms AND lineage)
  schema_diff.py            drift engine: 15 drift types, deterministic rename
                            matching (stable matching + confidence scores),
                            cast-safety classification
  lineage.py                LineageGraph, DAX Table[Column] parsing,
                            annotate_downstream() -> cross_layer_break and
                            cross_workspace_break records
  workspace.py              WorkspaceRegistry: JSON manifest of workspaces,
                            items, tenants and cross-workspace links
                            (docs/CROSS_WORKSPACE.md)
  schema_store.py           baseline JSON snapshots per layer (atomic writes,
                            corrupt files fail loudly)
  llm_reasoner.py           ClaudeReasoner (anthropic SDK, retries/backoff,
                            defensive JSON parsing) + MockReasoner
  git_handler.py            branch, apply TMDL find/replace fixes, PR via gh
                            CLI or GitHub REST
  notifications/
    base.py                 DriftAlert (built once) + NotificationChannel ABC
    dispatcher.py           fan-out, per-channel try/except, --dry-run
    console_channel.py      rich table (always on)
    slack_channel.py        Block Kit; webhook or chat.postMessage
    teams_channel.py        Adaptive Card; webhook or Graph channel message
    outlook_channel.py      HTML email; Graph sendMail or SMTP fallback
  prompts/                  the three Claude prompts, isolated for tuning
  agents/
    definitions.py          ten AgentSpecs (system prompt + tool whitelist
                            + turn cap) - the only file that grows per agent
    tools.py                ToolContext + guard-railed ToolRegistry (~20
                            tools over differ/lineage/backends/git/fab CLI)
    runtime.py              Anthropic tool-use loop: retries, turn + token
                            budgets, JSONL run logs; MockAgentRuntime offline
    __init__.py             list_agents() / run_agent() public surface
sample_data/
  load_adventureworks.py    deterministic AdventureWorksLT subset -> bronze.*
  build_medallion.py        silver.* + gold.* SQL transforms; semantic_model.json;
                            reports.json
  inject_drift.py           5 drift scenarios (rename/drop/type/nullability/add)
```

## Data flow of one cycle (`main.run_once`)

1. Backend snapshots all available layers into `LayerSchema` dataclasses.
2. `SchemaStore` loads the baseline snapshots. Missing or corrupt baselines
   **fail loudly** (`BaselineError`, exit code 3) — they are never silently
   recreated, because recapturing would swallow whatever drifted since the
   file vanished. Baselines are only written by an explicit `--baseline` run
   (atomic temp-file+rename writes).
3. `schema_diff.diff_all` produces per-layer `DriftRecord`s:
   - drop+add pairs are re-classified as **renames** when base type matches and
     ordinal position or name similarity (difflib ≥ 0.55) agrees; the pairing
     is a deterministic Gale–Shapley **stable matching** over confidence
     scores (name similarity + position + exact type + nullability + key
     agreement) with lexicographic tie-breakers, and each rename carries its
     confidence;
   - type changes are **warning** when the cast is widening/safe
     (`INTEGER→BIGINT`), **critical** otherwise (`DECIMAL→VARCHAR`);
   - same-base-type declarations with different params (`DECIMAL(19,4)→
     DECIMAL(10,2)`) become **`precision_scale_change`** — widening is a
     warning, narrowing is critical (data truncation);
   - shared columns whose *relative* order changed become **`column_reorder`**
     (dense-ranked so adds/drops don't cause false positives);
   - DAX measures are diffed too — **`measure_drop`** (critical),
     **`measure_add`** (info), **`measure_change`** (warning, whitespace
     normalized so TMDL reformatting isn't flagged).
4. `medallion.build_lineage_graph` assembles edges:
   - Bronze→Silver→Gold from the declared mappings;
   - Gold→model columns from each model table's `sourceTable`;
   - model columns→measures by regex-parsing `Table[Column]` refs out of DAX;
   - model columns/measures→report bindings from PBIP metadata.
5. `lineage.annotate_downstream` BFS-walks each breaking drift, fills
   `downstream_impact`, and synthesizes `cross_layer_break` records for
   impacted nodes in *other* layers (deduped). With a workspace manifest
   configured (`lineage.workspaces_manifest`), targets in a *different
   workspace* become **`cross_workspace_break`** records instead, annotated
   with the connecting link type (shortcut, OneLake shortcut, mirrored
   database, semantic-model binding…) and tenant boundary — see
   [CROSS_WORKSPACE.md](CROSS_WORKSPACE.md).
6. `llm_reasoner` (Claude or mock): impact JSON → severity adjustments,
   fix suggestions (exact TMDL find/replace), PR title/body/commit message.
   Analyses include workspace name, workspace path and per-workspace blast
   radius; multi-workspace impact is called out explicitly in the summary.
7. `git_handler` branches (`drift-fix/<timestamp>`), applies fixes under the
   configured PBIP folder, commits, pushes, opens the PR (gh CLI → REST
   fallback). Never touches the base branch. Dry-run prints instead.
8. `notifications.dispatcher` renders the single `DriftAlert` per channel and
   sends; failures are isolated and reported per channel.

Exit codes: `0` clean · `1` critical drift (CI gate) · `2` config error ·
`3` missing/corrupt baselines.

## Key decisions

* **Rename detection is heuristic by design — but deterministic.** Same-type +
  same-position or similar-name covers the common "rename in a transform"
  case; ambiguous pairs fall back to drop+add (safe: more severe, never
  less). The stable-matching pairing guarantees identical inputs always
  produce identical rename pairs, independent of dict ordering.
* **Baselines fail loudly.** A missing baseline could mean tampering or an
  operational mistake — either way, silently recapturing it would erase the
  very evidence a drift detector exists to keep. Recreation is an explicit
  operator action.
* **Cross-workspace topology is declarative.** The workspace manifest is a
  reviewed JSON document, not runtime API discovery — the lineage engine's
  claims about which workspace breaks stay auditable.
* **The mapping tables in `medallion.py` are the transformation contract.**
  `build_medallion.py` generates its SQL *from the same names*, so the lineage
  graph can't drift from the transforms in simulate mode. In live mode the
  equivalent manifest should be exported from your Dataflow/pipeline repo.
* **Measures are graph nodes** (`layer:Table#Measure`), so measure breakage is
  first-class, and report bindings can hang off measures as well as columns.
* **Baselines are plain JSON** — diffable, reviewable, committable if you want
  schema history in Git. Every save also archives a timestamped copy under
  `.baselines/history/` (fuel for the `historian` agent).
* **Two LLM layers, one toolkit.** The scheduled pipeline keeps the
  deterministic three-prompt reasoner (predictable cost, mockable CI); the
  agents (docs/AGENTS.md) add tool-use loops for interactive work. Both read
  the same backends, differ and lineage graph, so their evidence never
  diverges — and write access is gated identically (branch-only git, explicit
  --allow-writes).
