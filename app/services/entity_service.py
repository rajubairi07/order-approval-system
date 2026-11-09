"""Entity creation (workflow trigger) and the human approval action (D7/D8)."""

from decimal import Decimal

from sqlalchemy.orm import Session

from app import tools as db_tools
from app.agents.orchestrator import CAPABILITY_FOR_ENTITY, process_entity
from app.exceptions import AuthorizationError, NotFoundError
from app.graph.state import ApprovalState
from app.models import Order, Quote, User
from app.models.enums import EntityStatus, EntityType
from app.schemas.approval import EntityInfo
from app.services.evaluator import rule_matches
from app.tools.entity_tools import load_row as _load_entity_row

_ENTITY_MODEL = {EntityType.ORDER: Order, EntityType.QUOTE: Quote}
_VALUE_FIELD = {EntityType.ORDER: "order_value", EntityType.QUOTE: "quote_value"}

ACTION_TO_STATUS = {
    "APPROVE": EntityStatus.APPROVED,
    "REJECT": EntityStatus.REJECTED,
}


def create_and_process(
    db: Session, entity_type: EntityType, created_by: int, value: Decimal, llm=None
) -> tuple[EntityInfo, ApprovalState]:
    """Create the entity, then run the shared approval workflow on it."""
    creator = db.get(User, created_by)
    if creator is None:
        raise NotFoundError(f"Creator user {created_by} not found")

    row = _ENTITY_MODEL[entity_type](
        created_by=created_by, **{_VALUE_FIELD[entity_type]: value}
    )
    db.add(row)
    db.commit()

    result = process_entity(db, entity_type, row.id, llm)
    return db_tools.get_entity_details(db, entity_type, row.id), result


def _escalation_authority(db: Session, entity: EntityInfo, actor_role_ids: set[int]) -> bool:
    """D8: an escalated entity may be resolved by any active user holding the
    role of any rule matching its value — in seed data, Admin via the
    catch-all. No role name is hardcoded."""
    capability = CAPABILITY_FOR_ENTITY[entity.entity_type]
    for policy in db_tools.fetch_active_policies(db, entity.entity_type, capability):
        for rule in db_tools.get_approval_rules(db, policy.id):
            if rule_matches(rule, entity.value) and rule.allowed_role_id in actor_role_ids:
                return True
    return False


def apply_action(
    db: Session,
    entity_type: EntityType,
    entity_id: int,
    actor_id: int,
    action: str,
    remarks: str | None = None,
) -> EntityInfo:
    """Validate and apply a human APPROVE/REJECT decision.

    The entity row is locked (SELECT ... FOR UPDATE on PostgreSQL) for the
    whole check-and-transition, so concurrent duplicate actions serialize and
    the loser fails the state-machine check (D7).
    """
    new_status = ACTION_TO_STATUS.get(action)
    if new_status is None:
        raise ValueError(f"Unknown action '{action}'; expected APPROVE or REJECT")

    row = _load_entity_row(db, entity_type, entity_id, for_update=True)
    entity = db_tools.get_entity_details(db, entity_type, entity_id)

    actor = db_tools.get_user_details(db, actor_id)
    if not actor.is_active:
        raise AuthorizationError(f"User {actor_id} is inactive")

    if entity.status == EntityStatus.PENDING_APPROVAL:
        if actor_id != entity.current_approver:
            raise AuthorizationError(
                f"User {actor_id} is not the assigned approver for "
                f"{entity_type.value} {entity_id}"
            )
    elif entity.status == EntityStatus.ESCALATED:
        actor_role_ids = {r.id for r in db_tools.get_user_roles(db, actor_id)}
        if not _escalation_authority(db, entity, actor_role_ids):
            raise AuthorizationError(
                f"User {actor_id} lacks authority to resolve the escalated "
                f"{entity_type.value} {entity_id}"
            )

    # Raises InvalidTransitionError for terminal/CREATED states — including
    # the loser of a concurrent duplicate action.
    info = db_tools.update_entity_status(db, entity_type, entity_id, new_status)

    db_tools.persist_audit_log(
        db,
        entity_type,
        entity_id,
        action=f"human_{action.lower()}",
        performed_by=actor_id,
        agent_name=None,
        reasoning_trace=(
            f"{actor.name} (id {actor_id}) {action.lower()}d "
            f"{entity_type.value} {entity_id} from status {entity.status.value}."
        ),
        remarks=remarks,
    )
    db.commit()
    return info
