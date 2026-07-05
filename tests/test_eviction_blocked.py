"""Rubric failure case: a request to lock out a tenant is blocked.

The agent must never auto-execute an eviction or lockout — it is outside the
agent's authority. This test drives the full governance graph (stub mode, no
LLM) with a plan whose only step is ``tenant.lockout`` and asserts:

  - the reviewer blocks it (allow=False, violations include action_not_allowed)
  - no tool was called (no log_call line for tenant.lockout)
  - a block entry was written to the audit log
  - the chain still verifies

This doubles as a P7 regression test: swapping the plan action for
``incident.escalate`` with a sub-threshold risk_band_score exercises the same
"fail closed for a side-effect breach" path.
"""

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from governance.audit import AuditLogger
from governance.graph_builder import build_graph
from governance.graph_state import PlanStep
from governance.memory import SessionScratchpad
from governance.policy import Policy


@dataclass
class _StubPrompts:
    planner_system: str = "planner"
    worker_system: str = "worker"
    summarizer_system: str = "summarizer"


def _policy():
    return Policy.from_dict({
        "domain": "property_management",
        "actions": [
            {"name": "tenant.lockout", "side_effect": True, "allow": False,
             "require_human": True, "block_reason": "Lockout is outside agent authority."},
            {"name": "tenant.query", "side_effect": False, "allow": True},
        ],
    })


def _intake_factory(plan_action: str):
    """Intake that injects a one-step plan, bypassing the planner LLM."""
    def intake(state):
        state["plan"] = [PlanStep(action=plan_action, reason="user request",
                                  expected_side_effect=True)]
        state["redacted_text"] = f"please {plan_action}"
        return state
    return intake


def _run(plan_action: str):
    # ignore_cleanup_errors handles the Windows SQLite file-handle lingering
    # on temp-dir teardown. We snapshot the audit records inside the with
    # block (before cleanup) so assertions outside still see them.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        audit = AuditLogger(Path(d) / "audit.jsonl")
        memory = SessionScratchpad(Path(d) / "scratch.db")
        tool_registry = {"tenant.lockout": lambda **a: ("SHOULD_NOT_RUN", a),
                          "tenant.query": lambda **a: {"found": True}}
        graph = build_graph(
            policy=_policy(), tool_registry=tool_registry, tool_specs=[],
            audit=audit, llm=None, memory=memory,
            intake_fn=_intake_factory(plan_action), prompts=_StubPrompts(),
        )
        result = graph.invoke(
            {"user_id": "u1", "turn_id": "t1", "messages": []},
            config={"configurable": {"thread_id": "t1"}},
        )
        records = audit.read_all()
        chain_ok = audit.verify_chain()
    return result, records, chain_ok


def test_lockout_blocked():
    result, records, chain_ok = _run("tenant.lockout")
    review = result["review"]
    assert review.allow is False, f"expected block, got {review}"
    assert "action_not_allowed" in review.violations

    # No tool call was logged for the blocked action.
    calls = [r for r in records if r["kind"] == "call"]
    assert all(r["action"] != "tenant.lockout" for r in calls), \
        "a blocked action should never reach the tool"

    # A block entry was recorded with the right reason.
    blocks = [r for r in records if r["kind"] == "block"]
    assert len(blocks) == 1
    assert blocks[0]["action"] == "tenant.lockout"
    assert "outside agent authority" in blocks[0]["block_reason"]

    # The chain is intact.
    assert chain_ok is True


def test_readonly_still_allowed():
    # Sanity counterpoint: a read-only action goes through, logs a call, no block.
    result, records, chain_ok = _run("tenant.query")
    assert result["review"].allow is True
    calls = [r for r in records if r["kind"] == "call"]
    assert len(calls) == 1 and calls[0]["action"] == "tenant.query"
    assert chain_ok is True