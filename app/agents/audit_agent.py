"""Audit & Explainability Agent: persist the decision trail and apply state.

Deliberately deterministic (spec guideline: LLMs coordinate and explain;
deterministic Python enforces): its output is the compliance record, so the
per-step explanations already produced by the other agents are synthesized
into a final summary without another LLM round-trip.

This node is NOT wrapped by the graph's safe() fallback: a failure to persist
the audit trail must abort and roll back the whole run rather than commit an
unaudited decision.
"""

from sqlalchemy.orm import Session

from app import tools as db_tools
from app.graph.state import ApprovalState, AuditEntry
from app.models.enums import EntityStatus

AGENT_NAME = "AuditExplainabilityAgent"


def _apply_outcome(
    db: Session, state: ApprovalState, status: EntityStatus, *, with_approver: bool
) -> str | None:
    """Apply the outcome to the entity; on failure roll back the poisoned
    flush (nothing else is pending: agents only read, audit writes come
    after) and return the error text."""
    try:
        if with_approver and state.approver_id is not None:
            db_tools.assign_approver(db, state.entity_type, state.entity_id, state.approver_id)
        db_tools.update_entity_status(db, state.entity_type, state.entity_id, status)
        return None
    except Exception as exc:
        db.rollback()
        return str(exc)


def run_audit_agent(db: Session, state: ApprovalState) -> dict:
    final_status = state.final_status or EntityStatus.ESCALATED
    status_reason = state.status_reason
    remarks = None

    error = _apply_outcome(db, state, final_status, with_approver=True)
    if error is not None:
        # Report ESCALATED rather than a status the database does not hold,
        # and replace the now-stale reason from the routing decision.
        remarks = f"Entity update to {final_status.value} failed: {error}"
        final_status = EntityStatus.ESCALATED
        status_reason = "Entity update failed; escalated for manual handling."
        fallback_error = _apply_outcome(db, state, EntityStatus.ESCALATED, with_approver=False)
        if fallback_error is not None:
            remarks += f"; escalation fallback also failed: {fallback_error}"

    decision = state.decision
    steps = " ".join(e.reasoning_trace for e in state.audit_entries if e.reasoning_trace)
    summary = (
        f"{state.entity_type.value} {state.entity_id} (value {state.entity_value}) "
        f"-> {final_status.value}. {status_reason or ''} {steps}".strip()
    )
    final_entry = AuditEntry(
        action="final_decision",
        agent_name=AGENT_NAME,
        policy_id=decision.policy_id if decision else None,
        rule_applied=decision.required_rule_id if decision else None,
        tool_called="persist_audit_log",
        reasoning_trace=summary,
        remarks=remarks or state.error,
    )

    entries = [*state.audit_entries, final_entry]
    for step, entry in enumerate(entries, start=1):
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

    return {
        "final_status": final_status,
        "status_reason": status_reason,
        "steps_persisted": len(entries),
    }
