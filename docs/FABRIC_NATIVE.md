# Running inside Fabric (notebook + pipeline + CI/CD)

Three Fabric-native ways to run the detective **in the workspace it watches**,
using the notebook identity instead of client secrets. All artifacts live in
[`fabric/`](../fabric).

```
fabric/
├── DriftDetection.Notebook/          # notebook wrapper (Fabric Git format)
│   ├── .platform
│   └── notebook-content.py
├── DriftCheckPipeline.DataPipeline/  # scheduled orchestration + fail gate
│   ├── .platform
│   └── pipeline-content.json
├── deploy-config.yml                 # `fab deploy` (fabric-cicd) config
└── parameter.yml                     # per-environment value rewriting
```

## Auth: no secrets inside Fabric

`src/azure_auth.py` supports four methods via `FABRIC_AUTH_METHOD` (env) or
`fabric.auth_method` (config.yaml):

| method | identity | where |
|---|---|---|
| `client_secret` | SPN from `.env` | laptops, GitHub Actions (**default outside Fabric — unchanged**) |
| `notebookutils` | the notebook's executing identity via `notebookutils.credentials.getToken` | **inside a Fabric notebook** (auto-detected) |
| `managed_identity` | system/user-assigned MI (`AZURE_CLIENT_ID` optional for UAMI) | Azure VMs, Container Apps, AKS |
| `default` | `DefaultAzureCredential` chain | anything else |

Unset = auto-detect: `notebookutils` when the module is importable (i.e. you
are in a Fabric notebook), else `client_secret`. Graph mail/Teams calls use the
same credential — note that the *executing identity* (workspace identity or
the scheduling user) then needs the Graph permissions, not your SPN.

## 1. Notebook wrapper

`DriftDetection.Notebook` clones the repo at runtime, installs requirements,
overrides `config.yaml` with its parameters, and calls `run_once()` in-process.

Parameters (edit the cell, or feed from the pipeline):

| param | meaning |
|---|---|
| `REPO_URL` / `REPO_REF` | where to fetch this project (required) |
| `WORKSPACE_ID`, `LAKEHOUSE_ID`, `WAREHOUSE_ID`, `SEMANTIC_MODEL_ID` | Fabric item IDs (fallback: values in the repo's config.yaml) |
| `SQL_ENDPOINT` / `SQL_DATABASE` | optional, unlocks INFORMATION_SCHEMA detail |
| `DRY_RUN` | `True` renders notifications/PR without sending |
| `CAPTURE_BASELINE` | `True` snapshots baselines instead of diffing |
| `BASELINE_DIR` | defaults to `/lakehouse/default/Files/drift_baselines` so baselines **survive between runs** (attach a lakehouse to the notebook) |

The notebook exits with the critical-drift count
(`notebookutils.notebook.exit(str(n))`) so callers can gate on it.

Manual use: import via workspace **Git integration** (the `fabric/` folder
syncs both items automatically) or upload, attach a lakehouse, set params, Run.
First run with `CAPTURE_BASELINE = True`, subsequent runs `False`.

## 2. Data Factory pipeline

`DriftCheckPipeline` wraps the notebook in a `TridentNotebook` activity,
passes `repo_url` / `repo_ref` / `workspace_id` / `dry_run` pipeline
parameters through, then an **If Condition** checks the notebook's
`exitValue` and raises a `Fail` activity (`errorCode: SchemaDriftCritical`)
when critical drift exists — so drift shows up as a red pipeline run in
monitoring, alertable like any other pipeline failure.

Setup after deploy/import:
1. Open the pipeline → Parameters → set `repo_url` and `workspace_id`.
2. Confirm the Notebook activity points at `DriftDetection` (fabric-cicd
   rebinds it automatically; manual imports may need reselecting).
3. Add a schedule (e.g. nightly, after your bronze→gold pipeline).

## 3. CI/CD deployment (`fab deploy`)

Format verified against Microsoft Learn
([Tutorial – local deployment](https://learn.microsoft.com/fabric/cicd/tutorial-fabric-cicd-local)):

```bash
pip install --upgrade ms-fabric-cli     # deploy command needs a recent CLI
fab auth login
FABRIC_WORKSPACE_ID=<guid> bash scripts/deploy_fabric.sh
```

or manually:

```bash
cd fabric
# put your workspace GUID into deploy-config.yml, then
fab deploy --config deploy-config.yml
```

Multi-environment: `parameter.yml` rewrites the placeholder workspace GUID per
environment (`PROD`/`DEV`/`TEST`) at publish time — standard fabric-cicd
`find_replace`.

## Which mode when?

| scenario | run it as |
|---|---|
| local dev / demo | `fabric-drift --mode simulate --once --dry-run` |
| CI gate on a schedule, secrets OK | GitHub Actions (`.github/workflows/drift-check.yml`) |
| zero-secret, inside the tenant | **notebook + pipeline (this doc)** |
| promote through DEV→TEST→PROD | `fab deploy` + `parameter.yml` |

Caveats:
* The notebook clones from a **public repo or one the runtime can reach**; for
  private repos bake a PAT into `REPO_URL` via a Key Vault-backed variable
  library, or vendor the code into a Fabric environment/resource folder.
* Opening Git PRs from inside Fabric requires outbound GitHub access +
  `GITHUB_TOKEN`; the default notebook config keeps `DRY_RUN=True` and relies
  on notifications instead.
