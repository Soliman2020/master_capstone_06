"""Wires the governance nodes into a LangGraph StateGraph.

``build_graph`` takes all dependencies as arguments and returns a compiled
graph. Nothing domain-specific is hardcoded here — P7 calls ``build_graph``
with its own policy/tools/prompts/intake_fn and gets the same topology.

Graph:

    START -> ingest -> planner -> worker
    worker -> reviewer                       (always, before any tool runs)
    reviewer -> {allow: worker_dispatch, require_human: human_approval, block: summarizer}
    worker_dispatch -> worker                (loop to next plan step)
    worker -> {plan_done: summarizer, more_steps: reviewer}
    summarizer -> END
    human_approval -> worker_dispatch        (after a human approves)
"""

from __future__ import annotations

from typing import Callable

from langgraph.graph import END, START, StateGraph

from .audit import AuditLogger
from .graph_state import AgentState
from .memory import SessionScratchpad
from .nodes import (
    make_human_approval_node,
    make_planner_node,
    make_reviewer_node,
    make_summarizer_node,
    make_worker_dispatch_node,
    make_worker_node,
    route_after_review,
    route_after_worker,
)
from .policy import Policy


def build_graph(
    *,
    policy: Policy,
    tool_registry: dict[str, Callable],
    tool_specs: list,
    audit: AuditLogger,
    llm,
    memory: SessionScratchpad,
    intake_fn: Callable[[AgentState], AgentState],
    prompts,
    checkpointer=None,
):
    """Compile the agent graph. ``llm`` may be None for stub/test mode."""
    graph = StateGraph(AgentState)

    # Nodes.
    graph.add_node("ingest", intake_fn)
    graph.add_node("planner", make_planner_node(llm, prompts, memory, audit))
    graph.add_node("worker", make_worker_node(llm, prompts, tool_specs))
    graph.add_node("reviewer", make_reviewer_node(policy, audit))
    graph.add_node("worker_dispatch", make_worker_dispatch_node(tool_registry, audit))
    graph.add_node("summarizer", make_summarizer_node(llm, prompts, memory, audit))
    graph.add_node("human_approval", make_human_approval_node(audit))

    # Fixed edges.
    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "planner")
    graph.add_edge("planner", "worker")
    graph.add_edge("worker", "reviewer")          # always gate before any tool
    graph.add_edge("worker_dispatch", "worker")   # loop to next step
    graph.add_edge("human_approval", "worker_dispatch")
    graph.add_edge("summarizer", END)

    # Conditional edges.
    graph.add_conditional_edges(
        "reviewer",
        route_after_review,
        {"allow": "worker_dispatch", "require_human": "human_approval", "block": "summarizer"},
    )
    graph.add_conditional_edges(
        "worker",
        route_after_worker,
        {"summarizer": "summarizer", "worker": "reviewer"},
    )

    return graph.compile(checkpointer=checkpointer)