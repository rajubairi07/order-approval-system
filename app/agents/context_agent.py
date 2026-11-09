"""Context & Capability Agent: who created this, and may they transact?"""

from langchain_core.language_models.chat_models import BaseChatModel
from sqlalchemy.orm import Session

from app import tools as db_tools
from app.agents.base import run_agent_loop
from app.agents.toolkits import context_toolkit
from app.graph.state import ApprovalState, AuditEntry
from app.schemas.agents import ContextResult

AGENT_NAME = "ContextCapabilityAgent"

SYSTEM_PROMPT = f"""You are the {AGENT_NAME} in an order/quote approval workflow.
Your job: identify the transaction's creator and validate that they are active
and hold the capability the transaction requires. Use your tools for ALL facts
— never assume roles or capabilities. Steps: fetch the entity, then the
creator's details, roles and capabilities; finally call submit_context exactly
once with your validated result and a one-sentence explanation."""


def _authoritative(db: Session, state: ApprovalState) -> ContextResult:
    """Deterministic ground truth the LLM submission is checked against."""
    user = db_tools.get_user_details(db, state.creator_id)
    roles = db_tools.get_user_roles(db, state.creator_id)
    caps = db_tools.get_user_capabilities(db, state.creator_id)
    capability_ok = state.capability.value in caps and user.is_active
    return ContextResult(
        creator_active=user.is_active,
        creator_role_ids=sorted(r.id for r in roles),
        creator_role_names=sorted(r.name for r in roles),
        creator_capabilities=sorted(caps),
        capability_ok=capability_ok,
        explanation=(
            f"Creator {user.name} (active={user.is_active}) holds roles "
            f"{sorted(r.name for r in roles)} and capabilities {sorted(caps)}; "
            f"required capability {state.capability.value} is "
            f"{'present' if capability_ok else 'missing or unusable'}."
        ),
    )


def run_context_agent(db: Session, llm: BaseChatModel, state: ApprovalState) -> dict:
    tools, submit = context_toolkit(db)
    submitted, trace = run_agent_loop(
        llm,
        tools,
        submit,
        SYSTEM_PROMPT,
        context={
            "entity_type": state.entity_type.value,
            "entity_id": state.entity_id,
            "creator_id": state.creator_id,
            "entity_value": str(state.entity_value),
            "capability": state.capability.value,
        },
    )
    agent_result = ContextResult.model_validate(submitted)
    truth = _authoritative(db, state)

    # Guardrail (D9): the database-derived result is authoritative.
    compare = {"creator_active", "capability_ok"}
    overridden = agent_result.model_dump(include=compare) != truth.model_dump(
        include=compare
    ) or sorted(agent_result.creator_role_ids) != truth.creator_role_ids

    return {
        "creator_active": truth.creator_active,
        "creator_role_ids": truth.creator_role_ids,
        "creator_role_names": truth.creator_role_names,
        "creator_capabilities": truth.creator_capabilities,
        "capability_ok": truth.capability_ok,
        "audit_entries": [
            AuditEntry(
                action="validate_capability",
                agent_name=AGENT_NAME,
                tool_called=",".join(trace),
                reasoning_trace=truth.explanation,
                remarks=(
                    "Guardrail: agent submission diverged from database state and was overridden."
                    if overridden
                    else None
                ),
            )
        ],
    }
