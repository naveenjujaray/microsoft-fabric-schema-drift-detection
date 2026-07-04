#!/usr/bin/env bash
# Deploy the Fabric-native items (DriftDetection notebook + DriftCheckPipeline)
# into a workspace via the Fabric CLI's fabric-cicd integration.
#
# Usage:
#   FABRIC_WORKSPACE_ID=<guid> bash scripts/deploy_fabric.sh
#
# Requires: pip install --upgrade ms-fabric-cli  (deploy needs a recent CLI)
#           fab auth login (interactive or SPN; see docs/FABRIC_SETUP.md #1)
set -euo pipefail
cd "$(dirname "$0")/../fabric"

: "${FABRIC_WORKSPACE_ID:?set FABRIC_WORKSPACE_ID to the target workspace GUID}"

# stamp the workspace id into a throwaway copy of the deploy config
TMP_CONFIG="$(mktemp -t deploy-config-XXXX.yml)"
sed "s/<YOUR_WORKSPACE_ID>/${FABRIC_WORKSPACE_ID}/" deploy-config.yml > "$TMP_CONFIG"
trap 'rm -f "$TMP_CONFIG"' EXIT

fab deploy --config "$TMP_CONFIG"

echo "Deployed. In the workspace: open DriftCheckPipeline, set the repo_url /"
echo "workspace_id parameters, and add a nightly schedule."
