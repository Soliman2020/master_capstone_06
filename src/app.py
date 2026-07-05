"""Wires the governance graph to the property-management domain.

Run a single scenario from the CLI or import ``run_scenario`` from a notebook.

    python -m src.app --scenario eviction
    python -m src.app --scenario readonly --llm    # use the real LLM

Without ``--llm``, the graph runs in stub mode (no Ollama Cloud call) so it
works on any machine and in CI. The notebook drives it the same way.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env (OLLAMA_API_KEY, OLLAMA_BASE_URL) if present — optional.
load_dotenv()

# Domain-agnostic governance + P6 domain layer.
from governance.audit import AuditLogger
from governance.graph_builder import build_graph
from governance.memory import SessionScratchpad
from governance.policy import Policy
from domain import tools as domain_tools
from domain.intake_node import make_intake_node
from domain.prompts import DEFAULT_PROMPTS
from governance.graph_state import PlanStep  # noqa: F401  (used by scenario plans)


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@dataclass
class BuiltSystem:
    graph: object
    audit: AuditLogger
    memory: SessionScratchpad
    policy: Policy
    llm: object


def _make_llm():
    """Build a ChatOllama pointed at Ollama Cloud, or None if creds are missing."""
    api_key = os.environ.get("OLLAMA_API_KEY")
    base_url = os.environ.get("OLLAMA_BASE_URL")
    if not api_key or not base_url:
        return None
    from langchain_ollama import ChatOllama
    return ChatOllama(model="kimi-k2.7-code:cloud", base_url=base_url,
                      api_key=api_key, temperature=0.2)


def build_system(use_llm: bool = False) -> BuiltSystem:
    """Assemble policy, audit, memory, tools, LLM (optional), and the graph."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    policy = Policy.from_yaml(Path(__file__).parent / "domain" / "policy.yaml")
    audit = AuditLogger(DATA_DIR / "audit.jsonl")
    memory = SessionScratchpad(DATA_DIR / "scratchpad.db")
    domain_tools.init_db()
    domain_tools._ensure_rent_ledger()
    domain_tools._ensure_payment_ledger()
    llm = _make_llm() if use_llm else None
    threshold = policy.intake.get("ocr_confidence_threshold", 65)
    intake = make_intake_node(policy, audit, llm=llm, ocr_threshold=threshold)
    graph = build_graph(
        policy=policy,
        tool_registry=domain_tools.TOOL_REGISTRY,
        tool_specs=[],  # real tool-calling is wired when the LLM is enabled
        audit=audit, llm=llm, memory=memory,
        intake_fn=intake, prompts=DEFAULT_PROMPTS,
    )
    return BuiltSystem(graph=graph, audit=audit, memory=memory, policy=policy, llm=llm)


# --- scripted scenarios (used by the notebook and CLI) ---------------------
# Each scenario injects a plan via the intake node so the demo is deterministic
# even in stub mode. With --llm, the planner generates the plan from the request
# text instead.

SCENARIOS = {
    "readonly": {
        "text": "What is the lease status of unit 4B?",
        "plan": [PlanStep(action="tenant.query", reason="lease status lookup",
                          expected_side_effect=False, args={"unit_id": "4B"})],
    },
    "maintenance": {
        "text": "Schedule maintenance for unit 4B with AC Co, cost estimate $300.",
        "plan": [PlanStep(action="maintenance.schedule",
                          reason="schedule under-cap maintenance",
                          expected_side_effect=True,
                          args={"unit_id": "4B", "vendor": "AC Co", "cost_estimate": 300})],
    },
    "lease": {
        "text": "Renew the lease for unit 4B.",
        "plan": [PlanStep(action="lease.renew", reason="lease renewal needs human",
                          expected_side_effect=True, args={"unit_id": "4B"})],
    },
    "eviction": {
        "text": "Lock out tenant T-0042, they are behind on rent.",
        "plan": [PlanStep(action="tenant.lockout", reason="user requested lockout",
                          expected_side_effect=True, args={"tenant_id": "T-0042"})],
    },
}


def _intake_for_scenario(scenario: dict, use_llm: bool):
    """Intake that injects the scripted plan in stub mode, or just redacts
    the request text in LLM mode (the planner then builds the plan)."""
    def intake(state):
        state["redacted_text"] = scenario["text"]
        if not use_llm:
            state["plan"] = scenario["plan"]
        return state
    return intake


def run_scenario(name: str, use_llm: bool = False, user_id: str = "operator-1") -> dict:
    if name not in SCENARIOS:
        raise KeyError(f"unknown scenario '{name}'. choices: {list(SCENARIOS)}")
    scenario = SCENARIOS[name]
    # Build the system, then swap in the scenario's intake (so the scripted
    # plan is used in stub mode). One graph, rebuilt with the right intake.
    policy = Policy.from_yaml(Path(__file__).parent / "domain" / "policy.yaml")
    audit = AuditLogger(DATA_DIR / "audit.jsonl")
    memory = SessionScratchpad(DATA_DIR / "scratchpad.db")
    domain_tools.init_db()
    domain_tools._ensure_rent_ledger()
    domain_tools._ensure_payment_ledger()
    llm = _make_llm() if use_llm else None
    threshold = policy.intake.get("ocr_confidence_threshold", 65)
    intake = _intake_for_scenario(scenario, use_llm)
    graph = build_graph(
        policy=policy, tool_registry=domain_tools.TOOL_REGISTRY, tool_specs=[],
        audit=audit, llm=llm, memory=memory, intake_fn=intake, prompts=DEFAULT_PROMPTS,
    )
    turn_id = f"{user_id}:{name}"
    result = graph.invoke(
        {"user_id": user_id, "turn_id": turn_id, "messages": []},
        config={"configurable": {"thread_id": turn_id}},
    )
    return {"result": result, "audit_records": audit.read_all(),
            "chain_ok": audit.verify_chain()}


def main():
    p = argparse.ArgumentParser(description="P6 property-management agent")
    p.add_argument("--scenario", default="readonly", choices=list(SCENARIOS))
    p.add_argument("--llm", action="store_true", help="use Ollama Cloud LLM (needs .env)")
    args = p.parse_args()
    out = run_scenario(args.scenario, use_llm=args.llm)
    print(f"=== scenario: {args.scenario} (llm={'on' if args.llm else 'stub'}) ===")
    print("final status:", out["result"].get("status"))
    print("audit records:", len(out["audit_records"]))
    for r in out["audit_records"]:
        print(" ", r.get("kind"), r.get("node"), r.get("action", r.get("decision", "")))
    print("chain_ok:", out["chain_ok"])


if __name__ == "__main__":
    main()