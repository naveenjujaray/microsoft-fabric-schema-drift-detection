"""Prompt: PR title/body + conventional-commit message."""

COMMIT_MESSAGE_PROMPT = """\
You are writing a Git commit message and pull-request description for
automated schema-drift fixes in a Microsoft Fabric medallion project.

DRIFTS FIXED:
{drifts_json}

IMPACT SUMMARY:
{impact_summary}

Return ONLY valid JSON - no prose, no markdown fences - with this shape:
{{
  "commit_subject": "fix(semantic-model): rebind Email after silver rename",
  "commit_body": "Longer explanation, wrapped at 72 chars...",
  "pr_title": "...",
  "pr_body": "Markdown PR body: what drifted, what was fixed, what still needs a human."
}}

Rules:
- commit_subject: conventional-commit style, imperative, <= 72 characters.
- pr_body: include a '## Drift detected' section, a '## Fixes applied'
  section, and a '## Needs human review' section (may be empty).
"""
