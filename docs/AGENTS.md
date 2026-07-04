# Agents

Ten task-focused Claude agents run tool-use loops over the drift-detection
toolkit. One runtime, one guard-railed tool registry, ten declarative specs —
adding an agent is adding one `AgentSpec` in
[src/agents/definitions.py](../src/agents/definitions.py).

```bash
python main.py --list-agents
python main.py --agent <name> [--task "..."] [--allow-writes] [--max-turns N] [--mode simulate|live]
```

Requires `ANTHROPIC_API_KEY`. Without it every agent returns an explanatory
offline result (exit 1) instead of crashing — demos, tests and CI stay green.

## The ten agents

| Agent | Job | Writes? |
|---|---|---|
| `fix_verify` | Repair auto-fixable drift in a **propose → apply → re-diff → retry** loop until the diff verifies green | ✏️ |
| `drift_investigator` | Decide rename vs drop+add with **evidence** (column profiles, row samples, DAX refs), not just heuristics | – |
| `lineage_qa` | Interactive impact/provenance Q&A: *"what breaks if I drop X?"*, *"where does report field Y come from?"* | – |
| `root_cause` | Trace visible breakage **upstream** to its origin; group many symptoms under one root | – |
| `triage` | Rank current drift into a **P1/P2/P3 fix queue** by blast radius and report criticality | – |
| `migration_planner` | Ordered, reversible **migration plan** (validate → migrate → rollback per step) for non-auto-fixable drift; plan only, no DDL tools | – |
| `pr_responder` | Read reviewer comments on an auto-fix PR, adjust the TMDL edits, push a follow-up commit | ✏️ |
| `provisioner` | Drive the `fab` CLI to inspect/provision the workspace and write captured item IDs into config.yaml | ✏️ |
| `historian` | Mine archived baseline snapshots for drift **trends**: hotspot tables, recurring offenders | – |
| `notification_composer` | Compose **audience-tailored** messages (terse engineer Slack vs executive email) and preview exact channel payloads | – |

Examples:

```bash
# what-if analysis (read-only)
python main.py --agent lineage_qa --task "what breaks if silver.sales_orders.freight is dropped?"

# evidence-based rename verdicts after a suspicious deploy
python main.py --agent drift_investigator

# fix everything fixable, verifying each edit against a fresh diff
python main.py --agent fix_verify --allow-writes

# respond to review feedback on PR #42 (on the PR branch)
python main.py --agent pr_responder --task "address feedback on PR 42" --allow-writes

# provision live workspace and capture ids (fab CLI + SPN login required)
python main.py --mode live --agent provisioner \
  --task "inspect workspace SchemaDriftDemo, create missing lakehouse/warehouse, capture ids" \
  --allow-writes
```

## Architecture

```
main.py --agent NAME --task "..."
   │
   ▼
run_agent(name, task, ToolContext, llm_cfg)          src/agents/__init__.py
   │            │
   │            └─ ToolContext.build(mode, cfg)      backend + baselines + lineage graph
   ▼
AgentSpec (prompt, tool whitelist, turn cap)         src/agents/definitions.py
   │
   ▼
AgentRuntime.run()  ── messages.create(tools=…) ──►  Claude
   ▲                                                   │ tool_use blocks
   │  tool_result blocks                               ▼
   └────────────────  ToolRegistry.dispatch()        src/agents/tools.py
                        (guard rails below)
```

Every run writes a JSONL transcript (`.agent_runs/<ts>-<agent>.jsonl`): task,
each model turn, each tool call + result, finish/abort reason, token counts.

## Tools

| Tool | What | Write? |
|---|---|---|
| `run_diff` | full drift detection (baseline vs current + lineage breaks) | – |
| `get_schema` | current schema of one layer | – |
| `query_lineage` / `list_lineage_nodes` / `count_downstream_reports` | lineage walking + blast radius | – |
| `profile_column` / `sample_rows` / `run_sql` | data evidence (simulate/DuckDB only; SELECT-only, row-capped) | – |
| `grep_dax` / `read_report_metadata` | measure + report inspection | – |
| `workspace_map` | cross-workspace topology + per-workspace blast radius for a node | – |
| `read_file` | sandboxed repo file read | – |
| `list_baselines` / `diff_snapshots` | snapshot archive + historical diffs | – |
| `git_status` / `read_pr_comments` | repo + PR state (gh CLI) | – |
| `preview_notification` | render Slack/Teams/Outlook payload, never send | – |
| `apply_tmdl_edit` | exact find/replace under the reports dir | ✏️ |
| `git_commit_push` | commit+push current branch (refuses base branch) | ✏️ |
| `fab_run` | Fabric CLI; `ls/get/exists/auth` free, `create/mkdir/api/deploy` gated | ✏️* |
| `update_config_ids` | write captured Fabric GUIDs into config.yaml (allow-listed keys only) | ✏️ |

## Guard rails (production posture)

* **Write gating** — write tools refuse unless `--allow-writes`; agents then
  produce an exact plan instead. Notification sending and DDL are *not*
  available to any agent by design.
* **Sandboxes** — file reads confined to the repo (deny `.env`, `.git`); TMDL
  edits confined to the reports dir; SQL is single-statement SELECT/WITH with
  a keyword blocklist, read-only connection, 200-row cap.
* **Budgets** — per-agent turn cap (`--max-turns` to override), cumulative
  token budget (`llm.agents.max_total_tokens`, default 200k), bounded tool
  output (8k chars per result).
* **Resilience** — transient API errors retried with backoff; tool exceptions
  return `ERROR:` strings to the model instead of crashing the loop; API
  failure/turn cap/token exhaustion all end in a clean `AgentResult` with the
  reason.
* **Auditability** — full JSONL transcript per run; `AgentResult` carries
  turns, tool calls, token usage, stop reason, log path.
* **Git safety** — `git_commit_push` refuses the base branch; PR flow remains
  branch-only.

## Configuration

```yaml
llm:
  model: "claude-opus-4-6"        # agents inherit unless overridden
  agents:
    model: ""                     # optional agent-specific model
    max_total_tokens: 200000      # cumulative budget per run
    run_log_dir: ".agent_runs"    # JSONL transcripts
```

Exit codes for `--agent`: `0` success, `1` agent did not finish (offline, turn
cap, token budget, API error — see the printed stop reason), `2` config error.

## Relationship to the structured pipeline

`python main.py --once` (the scheduled detection path) still uses the
deterministic three-prompt reasoner — predictable cost, JSON-parseable output,
mock-friendly CI. Agents are the **interactive/operational layer** on top:
investigation, repair-with-verification, triage, provisioning. They share the
same backends, differ, lineage graph and notification renderers, so evidence
never diverges between the two paths.
