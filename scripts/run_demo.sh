#!/usr/bin/env bash
# One-command simulate-mode demo:
#   loads AdventureWorksLT -> builds medallion -> captures baseline ->
#   injects drift -> detects it -> (mock) Claude reasoning -> drift report
#   + PR body + all notification payloads rendered to console.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> 1/5 cleaning previous demo state"
rm -rf .baselines sample_data/warehouse.duckdb sample_data/generated

echo "==> 2/5 loading AdventureWorksLT into Bronze"
python -m sample_data.load_adventureworks

echo "==> 3/5 building Silver + Gold + semantic model + reports metadata"
python -m sample_data.build_medallion

echo "==> 4/5 capturing baseline schema snapshots"
python -m fabric_drift_detective --mode simulate --baseline

echo "==> 5/5 injecting drift and running detection"
python -m sample_data.inject_drift --scenario all
# exit code 1 = critical drift found (by design, usable as a CI gate);
# for the demo that's the expected success case.
python -m fabric_drift_detective --mode simulate --once --dry-run || true

echo "Demo complete. Re-run 'python -m fabric_drift_detective --mode simulate --once --dry-run' anytime."
