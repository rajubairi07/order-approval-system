"""Entity creation (workflow trigger) and the human approval action (D7/D8)."""

from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from app import tools as db_tools
from app.agents.orchestrator import (
    CAPABILITY_FOR_ENTITY,
    awaiting_human_decision,
    process_entity,
    resume_entity,
)
from app.exceptions import AuthorizationError, InvalidActionError, InvalidTransitionError, NotFoundError
from app.graph.state import ApprovalState
from app.models import User
from app.models.enums import EntityStatus, EntityType
from app.schemas.approval import EntityInfo
from app.services.evaluator import rule_matches
from app.tools.entity_tools import _ENTITY_MODEL, _VALUE_ATTR

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
        created_by=created_by, **{_VALUE_ATTR[entity_type]: value}
    )
    db.add(row)
    db.commit()

    result = process_entity(db, entity_type, row.id, llm)
    return db_tools.get_entity_details(db, entity_type, row.id), result


def _has_decision_authority(db: Session, entity: EntityInfo, actor_id: int) -> bool:
    """D8: may this user decide an entity outside the assigned-approver path?

    Mirrors routing eligibility so nobody can resolve an entity they could
    never be routed: the actor must not be the creator (no self-approval),
    must hold the entity's capability, and must hold the role of an active
    rule matching the value — in seed data, Admin via the catch-all. No role
    name is hardcoded.
    """
    if actor_id == entity.created_by:
        return False
    capability = CAPABILITY_FOR_ENTITY[entity.entity_type]
    if capability.value not in db_tools.get_user_capabilities(db, actor_id):
        return False
    actor_role_ids = {r.id for r in db_tools.get_user_roles(db, actor_id)}
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
    the loser fails the state-machine check (D7). Human actions are legal
    ONLY from PENDING_APPROVAL and ESCALATED: CREATED belongs to the
    workflow and terminal states are final (409 for everything else).
    """
    new_status = ACTION_TO_STATUS.get(action)
    if new_status is None:
        raise InvalidActionError(f"Unknown action '{action}'; expected APPROVE or REJECT")

    entity = db_tools.get_entity_details(db, entity_type, entity_id, for_update=True)

    actor = db_tools.get_user_details(db, actor_id)
    if not actor.is_active:
        raise AuthorizationError(f"User {actor_id} is inactive")

    if entity.status == EntityStatus.PENDING_APPROVAL:
        if actor_id != entity.current_approver:
            assigned = (
                db_tools.get_user_details(db, entity.current_approver)
                if entity.current_approver is not None
                else None
            )
            # If the assigned approver can no longer act, D8 authority
            # holders may decide, so staff churn cannot deadlock an entity.
            if (assigned is not None and assigned.is_active) or not _has_decision_authority(
                db, entity, actor_id
            ):
                raise AuthorizationError(
                    f"User {actor_id} is not the assigned approver for "
                    f"{entity_type.value} {entity_id}"
                )
    elif entity.status == EntityStatus.ESCALATED:
        if not _has_decision_authority(db, entity, actor_id):
            raise AuthorizationError(
                f"User {actor_id} lacks authority to resolve the escalated "
                f"{entity_type.value} {entity_id}"
            )
    else:
        raise InvalidTransitionError(
            f"No human action is permitted while {entity_type.value} {entity_id} "
            f"is {entity.status.value}"
        )

    # Human-in-the-loop (D10): if the workflow is parked at its interrupt for
    # this entity, resume that run so the decision becomes the next step of
    # the same execution. Authorisation above has already passed; the graph
    # only applies the transition and extends the trail.
    if awaiting_human_decision(entity_type, entity_id):
        resume_entity(
            db,
            entity_type,
            entity_id,
            {
                "actor_id": actor_id,
                "actor_name": actor.name,
                "action": action,
                "remarks": remarks,
            },
        )
        return db_tools.get_entity_details(db, entity_type, entity_id)

    # Fallback (HITL disabled, or the checkpoint is gone after a restart):
    # apply the decision directly. A human action is then its own single-step
    # execution, so the trail never mixes NULL step numbers with the
    # workflow's numbered steps.
    info = db_tools.update_entity_status(db, entity_type, entity_id, new_status)

    db_tools.persist_audit_log(
        db,
        entity_type,
        entity_id,
        execution_id=str(uuid4()),
        step_number=1,
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
