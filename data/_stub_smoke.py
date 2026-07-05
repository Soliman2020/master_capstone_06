"""Throwaway smoke test: run the governance graph in stub mode (no LLM).

Proves the topology + routing + audit log work before the Ollama Cloud LLM
is wired in. Not a deliverable — lives in data/ which is gitignored.
"""

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from governance.audit import AuditLogger
from governance.graph_builder import build_graph
from governance.graph_state import AgentState, PlanStep
from governance.memory import SessionScratchpad
from governance.policy import Policy


@dataclass
class StubPrompts:
    planner_system: str = "You are a planner."
    worker_system: str = "You are a worker."
    summarizer_system: str = "Summarize for the user."


def make_stub_policy() -> Policy:
    return Policy.from_dict({
        "domain": "smoke",
        "actions": [
            {"name": "thing.read", "side_effect": False, "allow": True},
            {"name": "thing.forbidden", "side_effect": True, "allow": False,
             "require_human": True, "block_reason": "outside authority"},
        ],
    })


def main():
    with tempfile.TemporaryDirectory() as d:
        audit = AuditLogger(Path(d) / "audit.jsonl")
        memory = SessionScratchpad(Path(d) / "scratch.db")
        tool_registry = {"thing.read": lambda **a: {"rows": 3, **a}}
        tool_specs = []  # stub mode; worker won't really call bind_tools

        # Intake that injects a one-step read-only plan, bypassing the planner LLM.
        def intake_fn(state):
            state["plan"] = [PlanStep(action="thing.read", reason="smoke", expected_side_effect=False)]
            state["redacted_text"] = "smoke test request"
            return state

        graph = build_graph(
            policy=make_stub_policy(), tool_registry=tool_registry, tool_specs=tool_specs,
            audit=audit, llm=None, memory=memory, intake_fn=intake_fn, prompts=StubPrompts(),
        )

        init: AgentState = {"user_id": "u1", "turn_id": "t1", "messages": []}
        result = graph.invoke(init, config={"configurable": {"thread_id": "t1"}})
        print("final state:", {k: v for k, v in result.items() if k in {"status", "step_index"}})
        print("audit lines:", len(audit.read_all()))
        assert audit.verify_chain() is True
        print("STUB SMOKE OK — graph ran end-to-end with no LLM")


if __name__ == "__main__":
    main()