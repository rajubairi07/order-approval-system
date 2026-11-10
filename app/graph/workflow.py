"""LangGraph topology: one shared StateGraph for ORDER and QUOTE.

START -> load_entity -> context -> policy -> authority -> routing -> audit -> END
with conditional short-circuits to audit whenever a node reaches a final
status (reject, auto-approve, escalate) or errors.

When human-in-the-loop is enabled and the outcome is PENDING_APPROVAL, the
run does not end at audit: it continues to human_decision, which interrupts
and checkpoints. The approver's action resumes that same run (spec D10).
"""

from functools import lru_cache

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.state import ApprovalState

#: Our own types travel in the checkpointed state. Declaring them explicitly
#: keeps deserialisation working under strict msgpack, which refuses
#: unregistered modules.
_SERDE = JsonPlusSerializer(
    allowed_msgpack_modules=[
        ("app.models.enums", "EntityType"),
        ("app.models.enums", "CapabilityType"),
        ("app.models.enums", "EntityStatus"),
        ("app.graph.state", "AuditEntry"),
        ("app.schemas.approval", "PolicyDecision"),
        ("app.schemas.approval", "DecisionOutcome"),
    ]
)

#: Process-local checkpointer. Interrupted runs live here between the HTTP
#: request that created the entity and the one that approves it, so it must
#: outlive a request but need not outlive the process - `apply_action` falls
#: back to applying the decision directly when no checkpoint is found.
CHECKPOINTER = InMemorySaver(serde=_SERDE)


def thread_id(entity_type, entity_id: int) -> str:
    """One checkpoint thread per entity, stable across requests."""
    return f"{entity_type.value}:{entity_id}"


def discard_thread(entity_type, entity_id: int) -> None:
    """Drop a finished run's checkpoint.

    Only runs parked at the human_decision interrupt need to survive, so
    everything else is released as soon as it completes rather than
    accumulating in memory for the lifetime of the process.
    """
    try:
        CHECKPOINTER.delete_thread(thread_id(entity_type, entity_id))
    except Exception:  # best-effort cleanup; never fail a request over it
        pass


def reset_checkpoints() -> None:
    """Drop every checkpoint. Used by tests, where entity ids repeat across
    fixtures while this process-local checkpointer does not reset."""
    CHECKPOINTER.storage.clear()
    CHECKPOINTER.writes.clear()
    CHECKPOINTER.blobs.clear()


def build_workflow():
    graph = StateGraph(ApprovalState)

    graph.add_node("load_entity", nodes.safe("load_entity")(nodes.load_entity_node))
    graph.add_node("context", nodes.safe("context")(nodes.context_node))
    graph.add_node("policy", nodes.safe("policy")(nodes.policy_node))
    graph.add_node("authority", nodes.safe("authority")(nodes.authority_node))
    graph.add_node("routing", nodes.safe("routing")(nodes.routing_node))
    # audit is deliberately NOT safe()-wrapped: it is the terminal persister,
    # so a swallowed failure there would commit an unaudited decision. Its
    # exceptions propagate to process_entity, which rolls back the run.
    graph.add_node("audit", nodes.audit_node)
    # Also unwrapped: an interrupt must propagate to the runtime, not be
    # swallowed and converted into an escalation by safe().
    graph.add_node("human_decision", nodes.human_decision_node)

    graph.add_edge(START, "load_entity")
    graph.add_conditional_edges("load_entity", nodes.after_load, ["audit", "context"])
    graph.add_conditional_edges("context", nodes.after_context, ["audit", "policy"])
    graph.add_conditional_edges("policy", nodes.after_policy, ["audit", "authority"])
    graph.add_conditional_edges("authority", nodes.after_authority, ["audit", "routing"])
    graph.add_edge("routing", "audit")
    graph.add_conditional_edges("audit", nodes.after_audit, ["human_decision", END])
    graph.add_edge("human_decision", END)

    return graph.compile(checkpointer=CHECKPOINTER)


@lru_cache(maxsize=1)
def get_workflow():
    return build_workflow()
