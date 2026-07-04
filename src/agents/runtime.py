"""Agent runtime: the Anthropic tool-use loop with production guard rails.

Budgets and safety:
    * turn cap per agent (spec.max_turns, overridable)
    * cumulative token budget (config ``agents.max_total_tokens``)
    * transient API errors retried with exponential backoff
    * every event (request, tool call, result, finish) appended to a
      JSONL run log under ``agents.run_log_dir``
    * no ANTHROPIC_API_KEY -> ``MockAgentRuntime`` returns an
      explanatory result instead of crashing (keeps CLI/tests/demos
      working offline)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from ..llm_reasoner import _is_retryable

if TYPE_CHECKING:  # pragma: no cover
    from .definitions import AgentSpec
    from .tools import ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-4-6"


@dataclass
class AgentResult:
    """Outcome of one agent run."""

    agent: str
    output: str
    success: bool
    turns: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    log_path: str | None = None
    stop_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "success": self.success,
            "turns": self.turns,
            "tool_calls": len(self.tool_calls),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "stop_reason": self.stop_reason,
            "log_path": self.log_path,
        }


class _RunLog:
    """Append-only JSONL trace of one agent run."""

    def __init__(self, log_dir: str | Path, agent: str) -> None:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = directory / f"{stamp}-{agent}.jsonl"

    def write(self, event: str, **data: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **data,
        }
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")


class AgentRuntime:
    """Drives one agent spec against the Anthropic messages API."""

    def __init__(
        self,
        registry: "ToolRegistry",
        model: str = DEFAULT_MODEL,
        max_tokens_per_call: int = 4096,
        max_total_tokens: int = 200_000,
        max_retries: int = 3,
        log_dir: str | Path = ".agent_runs",
        client: Any = None,
    ) -> None:
        self.registry = registry
        self.model = model
        self.max_tokens_per_call = max_tokens_per_call
        self.max_total_tokens = max_total_tokens
        self.max_retries = max_retries
        self.log_dir = log_dir
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client

    # ------------------------------------------------------------------
    def _call(self, system: str, messages: list[dict[str, Any]]) -> Any:
        @retry(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=2, min=2, max=60),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        )
        def _go() -> Any:
            return self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens_per_call,
                system=system,
                tools=self.registry.definitions(),
                messages=messages,
            )

        return _go()

    # ------------------------------------------------------------------
    def run(self, spec: "AgentSpec", user_input: str,
            max_turns: int | None = None) -> AgentResult:
        """Tool-use loop: call model, execute tools, repeat until done."""
        turn_cap = max_turns or spec.max_turns
        log = _RunLog(self.log_dir, spec.name)
        result = AgentResult(agent=spec.name, output="", success=False,
                             log_path=str(log.path))
        log.write("start", agent=spec.name, model=self.model,
                  tools=self.registry.names(), task=user_input[:2000])

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_input}
        ]

        for turn in range(1, turn_cap + 1):
            result.turns = turn
            if result.input_tokens + result.output_tokens > self.max_total_tokens:
                result.output = (
                    "Stopped: token budget exhausted before completion. "
                    "Partial transcript in the run log."
                )
                result.stop_reason = "token_budget"
                log.write("abort", reason="token_budget")
                return result

            try:
                response = self._call(spec.system_prompt, messages)
            except Exception as exc:  # noqa: BLE001 - report, don't crash CLI
                result.output = f"Agent aborted: Anthropic API error: {exc}"
                result.stop_reason = "api_error"
                log.write("abort", reason="api_error", error=str(exc))
                return result

            usage = getattr(response, "usage", None)
            if usage is not None:
                result.input_tokens += getattr(usage, "input_tokens", 0) or 0
                result.output_tokens += getattr(usage, "output_tokens", 0) or 0

            text_parts = [b.text for b in response.content if b.type == "text"]
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            log.write("model_turn", turn=turn,
                      stop_reason=response.stop_reason,
                      text="\n".join(text_parts)[:2000],
                      tool_calls=[{"name": t.name, "input": t.input}
                                  for t in tool_uses])

            if response.stop_reason != "tool_use" or not tool_uses:
                result.output = "\n".join(text_parts).strip()
                result.success = True
                result.stop_reason = response.stop_reason or "end_turn"
                log.write("finish", turns=turn,
                          tokens_in=result.input_tokens,
                          tokens_out=result.output_tokens)
                return result

            # execute tool calls, feed results back
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for tu in tool_uses:
                output = self.registry.dispatch(tu.name, dict(tu.input or {}))
                result.tool_calls.append(
                    {"turn": turn, "tool": tu.name, "input": tu.input,
                     "ok": not output.startswith("ERROR")}
                )
                log.write("tool_result", tool=tu.name, ok=not output.startswith("ERROR"),
                          output=output[:2000])
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": output,
                })
            messages.append({"role": "user", "content": tool_results})

        result.output = (
            f"Stopped: reached the {turn_cap}-turn cap before finishing. "
            "Partial work is logged; raise --max-turns to continue."
        )
        result.stop_reason = "turn_cap"
        log.write("abort", reason="turn_cap")
        return result


class MockAgentRuntime:
    """Offline stand-in used when no ANTHROPIC_API_KEY is configured.

    Does not fake reasoning; it explains what WOULD run (agent, tools,
    task) so demos, tests and CI never crash on a missing key.
    """

    def __init__(self, registry: "ToolRegistry") -> None:
        self.registry = registry

    def run(self, spec: "AgentSpec", user_input: str,
            max_turns: int | None = None) -> AgentResult:
        return AgentResult(
            agent=spec.name,
            success=False,
            stop_reason="no_api_key",
            output=(
                f"[offline] Agent '{spec.name}' needs ANTHROPIC_API_KEY to run.\n"
                f"Task it would work on: {user_input}\n"
                f"Tools it would use: {', '.join(self.registry.names())}\n"
                "Set the key in .env and re-run."
            ),
        )


def make_runtime(llm_config: dict[str, Any], registry: "ToolRegistry"):
    """Real runtime when a key exists, mock otherwise."""
    agents_cfg = llm_config.get("agents", {}) if "agents" in llm_config else {}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.warning("ANTHROPIC_API_KEY not set; agent runs offline (mock)")
        return MockAgentRuntime(registry)
    return AgentRuntime(
        registry=registry,
        model=agents_cfg.get("model") or llm_config.get("model", DEFAULT_MODEL),
        max_tokens_per_call=int(llm_config.get("max_tokens", 4096)),
        max_total_tokens=int(agents_cfg.get("max_total_tokens", 200_000)),
        max_retries=int(llm_config.get("max_retries", 3)),
        log_dir=agents_cfg.get("run_log_dir", ".agent_runs"),
    )
