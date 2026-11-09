"""Tools for reading and transitioning Order/Quote entities.

Transaction boundary: tools flush but never commit — the unit of work is
owned by the caller (graph runner or API request), so a multi-tool step
either fully persists or fully rolls back.
"""

from sqlalchemy.orm import Session

from app.exceptions import NotFoundError
from app.models import Order, Quote
from app.models.enums import EntityStatus, EntityType
from app.schemas.approval import EntityInfo
from app.services.state_machine import validate_transition

_ENTITY_MODEL = {EntityType.ORDER: Order, EntityType.QUOTE: Quote}
_VALUE_ATTR = {EntityType.ORDER: "order_value", EntityType.QUOTE: "quote_value"}


def load_row(db: Session, entity_type: EntityType, entity_id: int, *, for_update: bool = False):
    # populate_existing refreshes identity-map rows, so status checks never
    # run against attributes cached before a concurrent commit.
    query = db.query(_ENTITY_MODEL[entity_type]).populate_existing()
    if for_update:
        query = query.with_for_update()
    row = query.filter_by(id=entity_id).one_or_none()
    if row is None:
        raise NotFoundError(f"{entity_type.value} {entity_id} not found")
    return row


def _to_info(entity_type: EntityType, row) -> EntityInfo:
    return EntityInfo(
        entity_type=entity_type,
        id=row.id,
        created_by=row.created_by,
        value=getattr(row, _VALUE_ATTR[entity_type]),
        status=row.status,
        current_approver=row.current_approver,
    )


def get_entity_details(
    db: Session, entity_type: EntityType, entity_id: int, *, for_update: bool = False
) -> EntityInfo:
    """Fetch an order/quote as a uniform EntityInfo.

    for_update=True row-locks (SELECT ... FOR UPDATE on PostgreSQL) so a
    check-and-transition sequence serializes against concurrent actions (D7).
    """
    return _to_info(entity_type, load_row(db, entity_type, entity_id, for_update=for_update))


def update_entity_status(
    db: Session,
    entity_type: EntityType,
    entity_id: int,
    new_status: EntityStatus,
) -> EntityInfo:
    """Transition an entity's status, enforcing the state machine.

    Raises InvalidTransitionError for illegal transitions. Callers needing
    race protection take the row lock first via
    get_entity_details(for_update=True).
    """
    row = load_row(db, entity_type, entity_id)
    validate_transition(row.status, new_status)
    row.status = new_status
    db.flush()
    return _to_info(entity_type, row)


def assign_approver(
    db: Session, entity_type: EntityType, entity_id: int, approver_id: int
) -> EntityInfo:
    """Set current_approver on an entity."""
    row = load_row(db, entity_type, entity_id)
    row.current_approver = approver_id
    db.flush()
    return _to_info(entity_type, row)
