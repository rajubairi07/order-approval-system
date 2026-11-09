"""LangGraph topology: one shared StateGraph for ORDER and QUOTE.

START -> load_entity -> context -> policy -> authority -> routing -> audit -> END
with conditional short-circuits to audit whenever a node reaches a final
status (reject, auto-approve, escalate) or errors.
"""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.state import ApprovalState


def build_workflow():
    graph = StateGraph(ApprovalState)

    graph.add_node("load_entity", nodes.safe("load_entity")(nodes.load_entity_node))
    graph.add_node("context", nodes.safe("context")(nodes.context_node))
    graph.add_node("policy", nodes.safe("policy")(nodes.policy_node))
    graph.add_node("authority", nodes.safe("authority")(nodes.authority_node))
    graph.add_node("routing", nodes.safe("routing")(nodes.routing_node))
    graph.add_node("audit", nodes.safe("audit")(nodes.audit_node))

    graph.add_edge(START, "load_entity")
    graph.add_conditional_edges("load_entity", nodes.after_load, ["audit", "context"])
    graph.add_conditional_edges("context", nodes.after_context, ["audit", "policy"])
    graph.add_conditional_edges("policy", nodes.after_policy, ["audit", "authority"])
    graph.add_conditional_edges("authority", nodes.after_authority, ["audit", "routing"])
    graph.add_edge("routing", "audit")
    graph.add_edge("audit", END)

    return graph.compile()


@lru_cache(maxsize=1)
def get_workflow():
    return build_workflow()
