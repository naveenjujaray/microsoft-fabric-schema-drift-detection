#!/usr/bin/env bash
# Provision the medallion workspace in Microsoft Fabric with the Fabric CLI.
#
# Prereqs:
#   pip install ms-fabric-cli        (Python 3.10-3.12; `fab --version` to check)
#   An existing Fabric capacity + an Azure AD app registration (service principal)
#   with "Service principals can use Fabric APIs" enabled in tenant settings.
#
# Command syntax verified against Microsoft Learn:
#   learn.microsoft.com/rest/api/fabric/articles/fabric-command-line-interface
#   learn.microsoft.com/fabric/database/sql/deploy-cli
# SPN login flags follow the official fabric-cli reference
# (microsoft.github.io/fabric-cli). If your CLI version differs, run
# `fab auth login` interactively instead. See docs/FABRIC_SETUP.md for the
# REST fallbacks for anything your CLI build doesn't support.
set -euo pipefail

: "${AZURE_CLIENT_ID:?set in .env}"
: "${AZURE_CLIENT_SECRET:?set in .env}"
: "${AZURE_TENANT_ID:?set in .env}"
WORKSPACE="${FABRIC_WORKSPACE_NAME:-SchemaDriftDemo}"
CAPACITY="${FABRIC_CAPACITY_NAME:?set FABRIC_CAPACITY_NAME to your capacity}"

echo "==> auth (service principal)"
fab auth login -u "$AZURE_CLIENT_ID" -p "$AZURE_CLIENT_SECRET" --tenant "$AZURE_TENANT_ID"

echo "==> workspace (assigned to capacity)"
fab create "${WORKSPACE}.Workspace" -P "capacityName=${CAPACITY}"

echo "==> lakehouse (Bronze+Silver) and warehouse (Gold)"
fab create "${WORKSPACE}.Workspace/DriftLakehouse.Lakehouse" -P "enableSchemas=true"
fab create "${WORKSPACE}.Workspace/DriftWarehouse.Warehouse"

echo "==> orchestration pipeline (definition uploaded separately or via portal)"
fab create "${WORKSPACE}.Workspace/BronzeToGold.DataPipeline" || \
  echo "NOTE: if your CLI build rejects this, create via REST (docs/FABRIC_SETUP.md #5)"

echo "==> list everything and capture IDs for config.yaml"
fab ls "${WORKSPACE}.Workspace"
fab get "${WORKSPACE}.Workspace" -q "id"
fab get "${WORKSPACE}.Workspace/DriftLakehouse.Lakehouse" -q "id"
fab get "${WORKSPACE}.Workspace/DriftWarehouse.Warehouse" -q "id"

cat <<'EOF'
Next steps (portal or REST; see docs/FABRIC_SETUP.md):
  * Dataflow Gen2 for AdventureWorksLT -> Bronze ingestion (step 4)
  * Semantic model + PBIP Git integration (step 6)
  * Paste the printed IDs into config.yaml (step 7)
EOF
