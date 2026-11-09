"""Audit & Explainability Agent: persist the decision trail and apply state.

Deliberately deterministic (spec guideline: LLMs coordinate and explain;
deterministic Python enforces): its output is the compliance record, so the
per-step explanations already produced by the other agents are synthesized
into a final summary without another LLM round-trip.
"""

from sqlalchemy.orm import Session

from app import tools as db_tools
from app.graph.state import ApprovalState, AuditEntry
from app.models.enums import EntityStatus

AGENT_NAME = "AuditExplainabilityAgent"


def run_audit_agent(db: Session, state: ApprovalState) -> dict:
    final_status = state.final_status or EntityStatus.ESCALATED
    remarks = None

    try:
        if state.approver_id is not None:
            db_tools.assign_approver(db, state.entity_type, state.entity_id, state.approver_id)
        db_tools.update_entity_status(db, state.entity_type, state.entity_id, final_status)
    except Exception as exc:  # keep the audit trail even if the update fails
        remarks = f"Entity update failed: {exc}"
        final_status = state.final_status or EntityStatus.ESCALATED

    for step, entry in enumerate(state.audit_entries, start=1):
        db_tools.persist_audit_log(
            db,
            state.entity_type,
            state.entity_id,
            execution_id=state.execution_id,
            step_number=step,
            action=entry.action,
            agent_name=entry.agent_name,
            policy_id=entry.policy_id,
            rule_applied=entry.rule_applied,
            tool_called=entry.tool_called,
            reasoning_trace=entry.reasoning_trace,
            remarks=entry.remarks,
        )

    decision = state.decision
    steps = " ".join(e.reasoning_trace for e in state.audit_entries if e.reasoning_trace)
    summary = (
        f"{state.entity_type.value} {state.entity_id} (value {state.entity_value}) "
        f"-> {final_status.value}. {state.status_reason or ''} {steps}".strip()
    )
    db_tools.persist_audit_log(
        db,
        state.entity_type,
        state.entity_id,
        execution_id=state.execution_id,
        step_number=len(state.audit_entries) + 1,
        action="final_decision",
        agent_name=AGENT_NAME,
        policy_id=decision.policy_id if decision else None,
        rule_applied=decision.required_rule_id if decision else None,
        tool_called="persist_audit_log",
        reasoning_trace=summary,
        remarks=remarks or state.error,
    )

    return {"final_status": final_status}
