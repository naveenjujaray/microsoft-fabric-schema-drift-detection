# Security Policy

## Supported versions

This project is pre-1.0 and ships from `main`. Security fixes land on the
latest release only.

| Version | Supported |
|---------|-----------|
| 0.2.x   | ✅        |
| < 0.2   | ❌        |

## Reporting a vulnerability

**Do not open a public issue for security problems.**

Report privately through GitHub's
[private vulnerability reporting](https://github.com/naveenjujaray/microsoft-fabric-schema-drift-detection/security/advisories/new)
(Security → Report a vulnerability). If that is unavailable, email the
maintainer at **jujaraynaveen@gmail.com** with subject
`SECURITY: fabric-schema-drift-detection`.

Please include:

- affected version / commit,
- a description and impact assessment,
- reproduction steps or a proof of concept,
- any suggested remediation.

### What to expect

- **Acknowledgement:** within 5 business days.
- **Assessment + triage:** within 10 business days.
- **Fix or mitigation plan:** communicated after triage; timeline depends
  on severity and complexity.
- Coordinated disclosure — we will agree on a public disclosure date with
  you and credit you (if you wish) in the release notes.

## Scope

This is a defensive schema-drift detection tool for Microsoft Fabric. It
does not host a service; it runs as a CLI / Fabric notebook / pipeline
against the operator's own tenant. Security-relevant surfaces:

- **Credential handling** — Azure SPN, Fabric, HANA, Snowflake, GitHub
  and notification-channel secrets are read from environment variables /
  `.env` only, never from `config.yaml` and never logged. Reports of
  secrets leaking into logs, stdout, PR bodies, notification payloads, or
  the `.agent_runs/` transcripts are in scope.
- **LLM-driven git edits** — auto-fix paths are sandboxed (traversal,
  symlink, absolute-path, size and count guards). Sandbox-escape reports
  are in scope.
- **Agent tool sandbox** — read-only SQL enforcement, path allow-listing
  (`.env`/`.git` denied), write-gating behind `--allow-writes`.
- **Fabric / source REST + SQL calls** — parameterized queries, no
  interpolation of untrusted values into SQL.
- **Dependency vulnerabilities** — CI runs `pip-audit`; report anything
  it misses.

Out of scope: vulnerabilities in Microsoft Fabric, Azure, the Anthropic
API, or third-party drivers themselves (report those upstream); issues
requiring a malicious operator who already controls the config and
credentials.

## Handling secrets safely

- Copy `.env.example` to `.env`; never commit `.env` (it is git-ignored).
- Use least-privilege service principals and short-lived credentials.
- Prefer `managed_identity` / `notebookutils` auth on Azure/Fabric compute
  so no secrets touch disk (see [docs/FABRIC_NATIVE.md](docs/FABRIC_NATIVE.md)).
- Rotate any credential that may have been exposed in a log or PR.
