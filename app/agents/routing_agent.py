"""Routing & Workflow Agent: who should approve this transaction?"""

from langchain_core.language_models.chat_models import BaseChatModel
from sqlalchemy.orm import Session

from app import tools as db_tools
from app.agents.base import run_agent_loop
from app.agents.toolkits import routing_toolkit
from app.graph.state import ApprovalState, AuditEntry
from app.schemas.agents import RoutingResult

AGENT_NAME = "RoutingWorkflowAgent"

SYSTEM_PROMPT = f"""You are the {AGENT_NAME} in an order/quote approval workflow.
Your job: find an ACTIVE user who holds the required approver role and the
required capability, excluding the creator (no self-approval). Use your tool
for the candidate list; choose the first candidate (lowest user id) for
deterministic assignment, or submit null when no candidate exists. Call
submit_routing exactly once with a real fetched user id — never invent one."""


def run_routing_agent(db: Session, llm: BaseChatModel, state: ApprovalState) -> dict:
    decision = state.decision
    tools, submit = routing_toolkit(db)
    submitted, trace = run_agent_loop(
        llm,
        tools,
        submit,
        SYSTEM_PROMPT,
        context={
            "entity_type": state.entity_type.value,
            "entity_id": state.entity_id,
            "creator_id": state.creator_id,
            "capability": state.capability.value,
            "required_role_id": decision.required_role_id,
            "required_role_name": decision.required_role_name,
        },
    )
    agent_result = RoutingResult.model_validate(submitted)

    # Guardrail (D9): recompute eligibility; lowest user id wins (D7).
    candidates = db_tools.find_eligible_approvers(
        db, decision.required_role_id, state.capability, exclude_user_id=state.creator_id
    )
    truth_id = candidates[0].id if candidates else None
    overridden = agent_result.approver_id != truth_id

    if truth_id is not None:
        chosen = candidates[0]
        explanation = (
            f"Assigned approver {chosen.name} (id {chosen.id}), the first of "
            f"{len(candidates)} active eligible {decision.required_role_name} "
            f"holder(s) with the {state.capability.value} capability."
        )
    else:
        explanation = (
            f"No active user holds the required role {decision.required_role_name} "
            f"with the {state.capability.value} capability; escalating."
        )

    return {
        "approver_id": truth_id,
        "audit_entries": [
            AuditEntry(
                action="route_for_approval",
                agent_name=AGENT_NAME,
                policy_id=decision.policy_id,
                rule_applied=decision.required_rule_id,
                tool_called=",".join(trace),
                reasoning_trace=explanation,
                remarks=(
                    "Guardrail: agent submission diverged from eligibility query and was overridden."
                    if overridden
                    else None
                ),
            )
        ],
    }
