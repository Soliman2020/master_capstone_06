"""Shared state that flows through every node in the agent graph.

Domain-agnostic. The only domain-specific bit is ``domain_state``, a plain
dict that P6 fills with property-management context and P7 fills with
incident/zone context — so the governance nodes never need to know what's
inside it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict


@dataclass
class PlanStep:
    """One step the planner decided to take. ``action`` is a domain action name."""

    action: str
    reason: str = ""
    expected_side_effect: bool = False


@dataclass
class ActionIntent:
    """What the worker wants to do, parsed from the LLM's tool-call output."""

    action: str
    args: dict[str, Any] = field(default_factory=dict)
    side_effect: bool = False
    cost_estimate: float | None = None


@dataclass
class ToolResult:
    """Result of actually calling a tool after the reviewer approved it."""

    tool: str
    ok: bool
    summary: str
    payload: Any = None


@dataclass
class ReviewDecision:
    """The reviewer's verdict on an ActionIntent. Mirrors policy.ReviewDecision
    but lives in state so the graph routers can read it without re-importing policy."""

    allow: bool
    require_human: bool
    violations: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def route(self) -> str:
        if not self.allow:
            return "block"
        if self.require_human:
            return "require_human"
        return "allow"


class AgentState(TypedDict, total=False):
    # Who/what this turn is for.
    user_id: str
    turn_id: str
    # The conversation so far (langchain BaseMessages) + the redacted user text
    # for this turn (PII already scrubbed by the intake node).
    messages: list
    redacted_text: str
    # The plan and where we are in it.
    plan: list[PlanStep]
    step_index: int
    # The current action the worker extracted, the reviewer's verdict, and the
    # result of executing it (if approved).
    current_action: ActionIntent | None
    review: ReviewDecision | None
    tool_result: ToolResult | None
    # Memory cross-link (user_id:turn_id) so audit lines point at scratchpad rows.
    scratchpad_ref: str
    # Domain context — opaque to governance. P6: tenant/unit/lease. P7: incident/zone.
    domain_state: dict[str, Any]
    # Loop guard + lifecycle status.
    iteration: int
    status: Literal["planning", "executing", "blocked", "done"]