"""Prompt: classify drift severity + business impact."""

IMPACT_ANALYSIS_PROMPT = """\
You are a Microsoft Fabric data platform expert analyzing schema drift in a
medallion architecture (Bronze -> Silver -> Gold -> Semantic Model -> Power BI
reports).

Below is a list of detected schema drifts (JSON) plus the lineage context
showing which downstream assets each drift touches.

DETECTED DRIFTS:
{drifts_json}

LINEAGE CONTEXT (upstream node -> downstream nodes at risk):
{lineage_json}

For EACH drift, assess:
1. "severity": confirm or adjust ("info" | "warning" | "critical").
2. "impact": plain-English business impact in 1-2 sentences (who/what breaks:
   which dashboards, measures, refreshes fail).
3. "affected_reports": list of Power BI report names at risk (from lineage).
4. "fixable": "yes" | "no" | "partial" - can this be fixed mechanically
   (e.g. propagate a rename into TMDL) without human data-modeling decisions?
5. "recommended_action": one concrete next step.

Return ONLY valid JSON - no prose, no markdown fences - with this shape:
{{
  "analyses": [
    {{
      "drift_index": 0,
      "severity": "critical",
      "impact": "...",
      "affected_reports": ["..."],
      "fixable": "yes",
      "recommended_action": "..."
    }}
  ],
  "summary": "One-paragraph executive summary of the overall blast radius."
}}
"""
