"""LangGraph node functions and conditional-edge predicates.

Each node receives (state, config); the request's DB session and chat model
travel in config["configurable"], so one compiled graph serves all requests.
Every node is wrapped by safe(): an unexpected failure never leaves the
entity half-processed — it records the error and escalates (orchestrator's
fallback duty).
"""

from langchain_core.runnables import RunnableConfig

from app import tools as db_tools
from app.agents.audit_agent import run_audit_agent
from app.agents.context_agent import run_context_agent
from app.agents.policy_agent import run_policy_agent
from app.agents.routing_agent import run_routing_agent
from app.graph.state import ApprovalState, AuditEntry
from app.models.enums import EntityStatus
from app.schemas.approval import DecisionOutcome
from app.services.evaluator import creator_has_authority

ORCHESTRATOR_NAME = "SupervisorOrchestrator"


def _cfg(config: RunnableConfig):
    conf = config["configurable"]
    return conf["db"], conf["llm"]


def safe(node_name: str):
    """Wrap a node so unexpected failures escalate instead of crashing."""

    def decorator(fn):
        def wrapped(state: ApprovalState, config: RunnableConfig) -> dict:
            try:
                return fn(state, config)
            except Exception as exc:
                return {
                    "error": f"{node_name}: {exc}",
                    "final_status": EntityStatus.ESCALATED,
                    "status_reason": f"Unexpected failure in {node_name}; escalated for manual handling.",
                    "audit_entries": [
                        AuditEntry(
                            action="workflow_error",
                            agent_name=ORCHESTRATOR_NAME,
                            reasoning_trace=f"Unexpected failure in {node_name}: {exc}",
                        )
                    ],
                }

        return wrapped

    return decorator


def load_entity_node(state: ApprovalState, config: RunnableConfig) -> dict:
    """Ground the workflow inputs against the database row."""
    db, _ = _cfg(config)
    info = db_tools.get_entity_details(db, state.entity_type, state.entity_id)
    if info.status != EntityStatus.CREATED:
        raise ValueError(
            f"Workflow requires a CREATED entity; found {info.status.value}."
        )
    return {"creator_id": info.created_by, "entity_value": info.value}


def context_node(state: ApprovalState, config: RunnableConfig) -> dict:
    db, llm = _cfg(config)
    updates = run_context_agent(db, llm, state)
    if not updates["capability_ok"]:
        updates["final_status"] = EntityStatus.REJECTED
        updates["status_reason"] = (
            f"Workflow validation failure (not a business approval rejection): creator "
            f"lacks the required {state.capability.value} capability or is inactive."
        )
    return updates


def policy_node(state: ApprovalState, config: RunnableConfig) -> dict:
    db, llm = _cfg(config)
    updates = run_policy_agent(db, llm, state)
    decision = updates["decision"]
    if decision.outcome != DecisionOutcome.RULE_SELECTED:
        updates["final_status"] = EntityStatus.ESCALATED
        updates["status_reason"] = decision.explanation
    return updates


def authority_node(state: ApprovalState, config: RunnableConfig) -> dict:
    """Deterministic authority check (D2) — orchestrator logic, no LLM."""
    authorized = creator_has_authority(state.decision, set(state.creator_role_ids))
    updates: dict = {"creator_authorized": authorized}
    if authorized:
        matching = sorted(
            set(state.decision.matching_role_ids) & set(state.creator_role_ids)
        )
        updates["final_status"] = EntityStatus.APPROVED
        updates["status_reason"] = "Creator satisfies the applicable policy; auto-approved."
        trace = (
            f"Creator holds role id(s) {matching} matching an applicable rule of "
            f"policy {state.decision.policy_id}; auto-approved."
        )
    else:
        trace = (
            f"Creator roles {state.creator_role_names} do not match any applicable "
            f"rule; external approval by {state.decision.required_role_name} required."
        )
    updates["audit_entries"] = [
        AuditEntry(
            action="authority_check",
            agent_name=ORCHESTRATOR_NAME,
            policy_id=state.decision.policy_id,
            rule_applied=state.decision.required_rule_id,
            reasoning_trace=trace,
        )
    ]
    return updates


def routing_node(state: ApprovalState, config: RunnableConfig) -> dict:
    db, llm = _cfg(config)
    updates = run_routing_agent(db, llm, state)
    if updates["approver_id"] is not None:
        updates["final_status"] = EntityStatus.PENDING_APPROVAL
        updates["status_reason"] = "Routed to an eligible approver; awaiting decision."
    else:
        updates["final_status"] = EntityStatus.ESCALATED
        updates["status_reason"] = "No eligible approver exists; escalated."
    return updates


def audit_node(state: ApprovalState, config: RunnableConfig) -> dict:
    db, _ = _cfg(config)
    return run_audit_agent(db, state)


# --- conditional edges --------------------------------------------------


def _decided(state: ApprovalState) -> bool:
    return state.error is not None or state.final_status is not None


def after_load(state: ApprovalState) -> str:
    return "audit" if _decided(state) else "context"


def after_context(state: ApprovalState) -> str:
    return "audit" if _decided(state) else "policy"


def after_policy(state: ApprovalState) -> str:
    return "audit" if _decided(state) else "authority"


def after_authority(state: ApprovalState) -> str:
    return "audit" if _decided(state) else "routing"
