"""Tool for persisting audit records (spec decision D6)."""

from sqlalchemy.orm import Session

from app.models import ApprovalLog
from app.models.enums import EntityType


def persist_audit_log(
    db: Session,
    entity_type: EntityType,
    entity_id: int,
    action: str,
    *,
    performed_by: int | None = None,
    agent_name: str | None = None,
    policy_id: int | None = None,
    rule_applied: int | None = None,
    tool_called: str | None = None,
    reasoning_trace: str | None = None,
    remarks: str | None = None,
) -> int:
    """Append one audit row for an agent/user action; returns the log id.

    reasoning_trace is a concise decision explanation, never raw LLM
    chain-of-thought.
    """
    log = ApprovalLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        performed_by=performed_by,
        agent_name=agent_name,
        policy_id=policy_id,
        rule_applied=rule_applied,
        tool_called=tool_called,
        reasoning_trace=reasoning_trace,
        remarks=remarks,
    )
    db.add(log)
    db.flush()
    return log.id
