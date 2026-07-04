"""Agentic layer: Claude tool-use loops over the drift-detective toolbox.

Ten task-scoped agents share one runtime (``runtime.AgentRuntime``) and
one guard-railed tool registry (``tools.ToolRegistry``). Each agent is
an ``AgentSpec``: a system prompt + a whitelist of tools + a turn cap.

Public surface:
    list_agents()            -> {name: description}
    run_agent(name, ...)     -> AgentResult
"""

from .definitions import AGENT_SPECS
from .runtime import AgentResult, AgentRuntime, MockAgentRuntime, make_runtime
from .tools import ToolContext, ToolRegistry, build_registry

__all__ = [
    "AGENT_SPECS",
    "AgentResult",
    "AgentRuntime",
    "MockAgentRuntime",
    "ToolContext",
    "ToolRegistry",
    "build_registry",
    "list_agents",
    "run_agent",
    "make_runtime",
]


def list_agents() -> dict[str, str]:
    """Agent name -> one-line description."""
    return {name: spec.description for name, spec in AGENT_SPECS.items()}


def run_agent(
    name: str,
    user_input: str,
    context: "ToolContext",
    llm_config: dict | None = None,
) -> AgentResult:
    """Run one named agent to completion.

    Raises KeyError for unknown agents. Uses the mock runtime when no
    ANTHROPIC_API_KEY is available (returns an explanatory result
    instead of failing).
    """
    spec = AGENT_SPECS[name]
    registry = build_registry(context, allowed=spec.tools)
    runtime = make_runtime(llm_config or {}, registry)
    return runtime.run(spec, user_input)
