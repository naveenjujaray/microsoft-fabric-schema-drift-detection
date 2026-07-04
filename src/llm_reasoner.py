"""Claude-powered drift reasoning via the Anthropic SDK.

Three jobs:
    a) classify each drift's severity + business impact
    b) propose concrete TMDL fixes for auto-fixable drifts
    c) write the PR title/body and commit message

``MockReasoner`` provides deterministic output for tests, demos and
CI runs without an API key. Both share the same interface, so the
pipeline never cares which one it holds.

Secrets are never sent to the API: only drift metadata (table/column
names, types) and TMDL excerpts go into prompts.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any, Protocol

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from .prompts import (
    COMMIT_MESSAGE_PROMPT,
    FIX_SUGGESTION_PROMPT,
    IMPACT_ANALYSIS_PROMPT,
)
from .schema_diff import DriftRecord, DriftType

if TYPE_CHECKING:  # pragma: no cover
    from .workspace import WorkspaceRegistry

logger = logging.getLogger(__name__)

CROSS_WORKSPACE_SENTENCE = (
    "This schema change impacts assets across multiple Microsoft Fabric "
    "workspaces."
)

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_llm_json(text: str) -> dict[str, Any]:
    """Defensively parse JSON from an LLM response.

    Strips markdown fences; falls back to extracting the outermost
    ``{...}`` block; returns ``{}`` on total failure rather than
    raising, so a flaky response degrades gracefully.
    """
    cleaned = _JSON_FENCE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            pass
    logger.warning("LLM returned unparseable JSON; using empty result")
    return {}


def _drifts_json(drifts: list[DriftRecord]) -> str:
    return json.dumps([d.to_dict() for d in drifts], indent=2, default=str)


def _lineage_json(drifts: list[DriftRecord]) -> str:
    return json.dumps(
        {
            f"{d.layer.value}:{d.table}.{d.column or '*'}": d.downstream_impact
            for d in drifts
            if d.downstream_impact
        },
        indent=2,
    )


def _workspace_json(workspaces: "WorkspaceRegistry | None") -> str:
    """Compact topology description for the impact prompt."""
    if workspaces is None:
        return "{}"
    return json.dumps(
        {
            "tenant_id": workspaces.tenant_id,
            "workspaces": [
                {
                    "name": ws.name,
                    "workspace_id": ws.workspace_id,
                    "tenant_id": ws.tenant_id,
                    "items": [
                        {
                            "name": item.name,
                            "type": item.item_type,
                            "item_id": item.item_id,
                            "layers": [layer.value for layer in item.layers],
                        }
                        for item in ws.items
                    ],
                }
                for ws in workspaces.workspaces
            ],
            "links": [
                {
                    "type": link.link_type,
                    "from": f"{link.src_workspace}:{link.src_layer.value}",
                    "to": f"{link.dst_workspace}:{link.dst_layer.value}",
                }
                for link in workspaces.links
            ],
        },
        indent=2,
    )


def _primary_node(d: DriftRecord) -> str:
    if d.column and not d.column.startswith("[measure] "):
        return f"{d.layer.value}:{d.table}.{d.column}"
    if d.column:
        return f"{d.layer.value}:{d.table}#{d.column.removeprefix('[measure] ')}"
    return f"{d.layer.value}:{d.table}"


class Reasoner(Protocol):
    """Interface shared by the live Claude reasoner and the mock."""

    def analyze_impact(self, drifts: list[DriftRecord]) -> dict[str, Any]: ...

    def suggest_fixes(
        self, drifts: list[DriftRecord], tmdl_excerpt: str
    ) -> dict[str, Any]: ...

    def write_pr_content(
        self, drifts: list[DriftRecord], impact_summary: str
    ) -> dict[str, Any]: ...


def _is_retryable(exc: BaseException) -> bool:
    """Retry on rate limits / transient API errors, not on bad requests."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return False
    return isinstance(
        exc,
        (
            anthropic.RateLimitError,
            anthropic.APIConnectionError,
            anthropic.InternalServerError,
        ),
    )


class ClaudeReasoner:
    """Live reasoner backed by the Anthropic API."""

    def __init__(
        self,
        model: str = "claude-opus-4-6",
        max_tokens: int = 4096,
        max_retries: int = 3,
        api_key: str | None = None,
        workspaces: "WorkspaceRegistry | None" = None,
    ) -> None:
        import anthropic

        self.model = model
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.workspaces = workspaces
        self.client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )

    def _complete(self, prompt: str) -> str:
        @retry(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=2, min=2, max=60),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        )
        def _call() -> str:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(
                block.text for block in message.content if block.type == "text"
            )

        return _call()

    # ------------------------------------------------------------------
    def analyze_impact(self, drifts: list[DriftRecord]) -> dict[str, Any]:
        prompt = IMPACT_ANALYSIS_PROMPT.format(
            drifts_json=_drifts_json(drifts),
            lineage_json=_lineage_json(drifts),
            workspace_json=_workspace_json(self.workspaces),
        )
        return parse_llm_json(self._complete(prompt))

    def suggest_fixes(
        self, drifts: list[DriftRecord], tmdl_excerpt: str
    ) -> dict[str, Any]:
        fixable = [d for d in drifts if d.auto_fixable]
        if not fixable:
            return {"fixes": []}
        prompt = FIX_SUGGESTION_PROMPT.format(
            drifts_json=_drifts_json(fixable), tmdl_excerpt=tmdl_excerpt
        )
        return parse_llm_json(self._complete(prompt))

    def write_pr_content(
        self, drifts: list[DriftRecord], impact_summary: str
    ) -> dict[str, Any]:
        prompt = COMMIT_MESSAGE_PROMPT.format(
            drifts_json=_drifts_json(drifts), impact_summary=impact_summary
        )
        return parse_llm_json(self._complete(prompt))


class MockReasoner:
    """Deterministic reasoner for demos/tests: no API key, no network."""

    def __init__(self, workspaces: "WorkspaceRegistry | None" = None) -> None:
        self.workspaces = workspaces

    def analyze_impact(self, drifts: list[DriftRecord]) -> dict[str, Any]:
        analyses = []
        for i, d in enumerate(drifts):
            reports = sorted(
                {
                    n.split(":", 1)[1].split(".", 1)[0]
                    for n in d.downstream_impact
                    if n.startswith("reports:")
                }
            )
            analysis: dict[str, Any] = {
                "drift_index": i,
                "severity": d.severity.value,
                "impact": (
                    f"{d.drift_type.value} on {d.layer.value}."
                    f"{d.table}.{d.column or '*'} affects "
                    f"{len(d.downstream_impact)} downstream asset(s)."
                ),
                "affected_reports": reports,
                "fixable": "yes" if d.auto_fixable else "no",
                "recommended_action": (
                    "Propagate change downstream via PR"
                    if d.auto_fixable
                    else "Escalate to data engineering for manual fix"
                ),
                "workspace": d.workspace,
            }
            if self.workspaces is not None:
                radius = self.workspaces.blast_radius(d.downstream_impact)
                analysis["workspace_path"] = self.workspaces.workspace_path(
                    _primary_node(d)
                )
                analysis["affected_workspaces"] = [
                    {"workspace": name, "assets": count}
                    for name, count in radius.items()
                ]
            analyses.append(analysis)

        critical = sum(1 for d in drifts if d.severity.value == "critical")
        summary = (
            f"{len(drifts)} drift(s) detected, {critical} critical. "
            "Cross-layer lineage identifies impacted Gold tables, DAX "
            "measures and Power BI reports; see analyses for detail."
        )
        impacted_workspaces = {d.workspace for d in drifts if d.workspace}
        has_ws_break = any(
            d.drift_type is DriftType.CROSS_WORKSPACE_BREAK for d in drifts
        )
        if has_ws_break or len(impacted_workspaces) > 1:
            names = ", ".join(sorted(impacted_workspaces))
            summary += (
                f" {CROSS_WORKSPACE_SENTENCE}"
                f" Impacted workspaces: {names}."
            )
        return {"analyses": analyses, "summary": summary}

    def suggest_fixes(
        self, drifts: list[DriftRecord], tmdl_excerpt: str
    ) -> dict[str, Any]:
        fixes = []
        for i, d in enumerate(drifts):
            if not d.auto_fixable or d.drift_type.value != "column_rename":
                continue
            fixes.append(
                {
                    "drift_index": i,
                    "file": f"definition/tables/{d.table}.tmdl",
                    "description": f"Rebind {d.old} -> {d.new}",
                    "find": f"sourceColumn: {d.old}",
                    "replace": f"sourceColumn: {d.new}",
                }
            )
        return {"fixes": fixes}

    def write_pr_content(
        self, drifts: list[DriftRecord], impact_summary: str
    ) -> dict[str, Any]:
        criticals = [d for d in drifts if d.severity.value == "critical"]
        subject = (
            f"fix(schema-drift): repair {len(criticals)} critical drift(s)"
            if criticals
            else "chore(schema-drift): record schema changes"
        )[:72]
        drift_lines = "\n".join(f"- {d.describe()}" for d in drifts)
        fixable = "\n".join(
            f"- {d.describe()}" for d in drifts if d.auto_fixable
        ) or "_none_"
        manual = "\n".join(
            f"- {d.describe()}" for d in drifts if not d.auto_fixable
        ) or "_none_"
        return {
            "commit_subject": subject,
            "commit_body": impact_summary,
            "pr_title": subject,
            "pr_body": (
                "## Drift detected\n"
                f"{drift_lines}\n\n"
                "## Fixes applied\n"
                f"{fixable}\n\n"
                "## Needs human review\n"
                f"{manual}\n\n"
                f"---\n{impact_summary}\n"
            ),
        }


def make_reasoner(
    llm_config: dict[str, Any],
    workspaces: "WorkspaceRegistry | None" = None,
) -> Reasoner:
    """Factory: Claude if enabled + key present, else the mock."""
    enabled = llm_config.get("enabled", True)
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if enabled and has_key:
        return ClaudeReasoner(
            model=llm_config.get("model", "claude-opus-4-6"),
            max_tokens=int(llm_config.get("max_tokens", 4096)),
            max_retries=int(llm_config.get("max_retries", 3)),
            workspaces=workspaces,
        )
    if enabled and not has_key:
        logger.warning("ANTHROPIC_API_KEY not set; falling back to MockReasoner")
    return MockReasoner(workspaces=workspaces)
