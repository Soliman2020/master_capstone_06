"""Wires the governance nodes into a LangGraph StateGraph.

``build_graph`` takes all dependencies as arguments and returns a compiled
graph. Nothing domain-specific is hardcoded here — P7 calls ``build_graph``
with its own policy/tools/prompts/intake_fn and gets the same topology, which is
the whole "Path C" reuse claim: the graph shell is reused, only the injected
domain pieces change.

Graph:

    START -> ingest -> planner -> worker
    worker -> reviewer                       (always, before any tool runs)
    reviewer -> {allow: worker_dispatch, require_human: human_approval, block: summarizer}
    worker_dispatch -> {more steps: reviewer, done: summarizer}   (the plan loop)
    human_approval -> worker_dispatch        (after a human approves)
    summarizer -> END

``checkpointer`` is a parameter (default None) so P7 can pass a SqliteSaver for
multi-session resume while P6 stays single-process with no checkpointing.
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
    route_after_dispatch,
    route_after_review,
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
    # The worker NEVER calls a tool directly — it always goes to the reviewer
    # first. This edge is the whole "non-bypassable gate" guarantee: the only
    # way to reach worker_dispatch (which actually runs the tool) is through
    # the reviewer's "allow" route.
    graph.add_edge("worker", "reviewer")
    graph.add_edge("human_approval", "worker_dispatch")
    graph.add_edge("summarizer", END)

    # Conditional edges.
    # After the reviewer: allow -> run the tool; require_human -> suspend for
    # approval; block -> skip the tool and go straight to the summary/refusal.
    graph.add_conditional_edges(
        "reviewer",
        route_after_review,
        {"allow": "worker_dispatch", "require_human": "human_approval", "block": "summarizer"},
    )
    # After a tool ran, loop back for the next plan step (reviewer gates it
    # again) or finish the turn if all steps are done. This is the plan loop.
    graph.add_conditional_edges(
        "worker_dispatch",
        route_after_dispatch,
        {"summarizer": "summarizer", "reviewer": "reviewer"},
    )

    return graph.compile(checkpointer=checkpointer)