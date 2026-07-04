"""Prompt: propose exact TMDL edits for auto-fixable drifts."""

FIX_SUGGESTION_PROMPT = """\
You are a Power BI / TMDL expert. The following schema drifts were detected
in a Fabric medallion architecture and judged auto-fixable:

AUTO-FIXABLE DRIFTS:
{drifts_json}

CURRENT TMDL / SEMANTIC MODEL DEFINITION (relevant excerpts):
{tmdl_excerpt}

For each drift, produce the exact edit needed to keep the semantic model and
reports working. A rename in Gold, for example, requires updating the model
column's source binding (and any DAX referencing it); a new column may need a
new model column added.

Return ONLY valid JSON - no prose, no markdown fences - with this shape:
{{
  "fixes": [
    {{
      "drift_index": 0,
      "file": "definition/tables/Customer.tmdl",
      "description": "Rebind Email column to renamed source column",
      "find": "sourceColumn: email",
      "replace": "sourceColumn: email_address"
    }}
  ]
}}

Rules:
- "find" must be an exact substring of the current definition.
- Never invent columns or tables not present in the definition.
- If a drift cannot actually be fixed mechanically, omit it.
"""
