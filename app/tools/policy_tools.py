"""Tools for reading approval policies and their rules."""

from sqlalchemy.orm import Session

from app.models import ApprovalPolicy, ApprovalRule
from app.models.enums import CapabilityType, EntityType
from app.schemas.approval import PolicyInfo, RuleInfo


def fetch_active_policies(
    db: Session, entity_type: EntityType, capability: CapabilityType
) -> list[PolicyInfo]:
    """Fetch active policies for an entity type + capability.

    Returns a list on purpose: more than one active policy is a conflict the
    evaluator must surface, not a case to silently pick from (spec D3).
    """
    rows = (
        db.query(ApprovalPolicy)
        .filter_by(entity_type=entity_type, capability=capability, is_active=True)
        .order_by(ApprovalPolicy.id)
        .all()
    )
    return [PolicyInfo.model_validate(p) for p in rows]


def get_approval_rules(db: Session, policy_id: int) -> list[RuleInfo]:
    """Fetch all rules of a policy, highest precedence (lowest priority) first."""
    rows = (
        db.query(ApprovalRule)
        .filter_by(policy_id=policy_id)
        .order_by(ApprovalRule.priority, ApprovalRule.min_value)
        .all()
    )
    return [
        RuleInfo(
            id=r.id,
            policy_id=r.policy_id,
            min_value=r.min_value,
            max_value=r.max_value,
            allowed_role_id=r.allowed_role_id,
            allowed_role_name=r.allowed_role.name,
            priority=r.priority,
        )
        for r in rows
    ]
