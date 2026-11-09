"""Approval state machine (spec state diagram).

APPROVED and REJECTED are terminal. ESCALATED can still be resolved by a
user with matching-rule authority (spec decision D8).
"""

from app.exceptions import InvalidTransitionError
from app.models.enums import EntityStatus

VALID_TRANSITIONS: dict[EntityStatus, frozenset[EntityStatus]] = {
    EntityStatus.CREATED: frozenset(
        {
            EntityStatus.APPROVED,
            EntityStatus.PENDING_APPROVAL,
            EntityStatus.REJECTED,
            EntityStatus.ESCALATED,
        }
    ),
    EntityStatus.PENDING_APPROVAL: frozenset(
        {EntityStatus.APPROVED, EntityStatus.REJECTED}
    ),
    EntityStatus.ESCALATED: frozenset(
        {EntityStatus.APPROVED, EntityStatus.REJECTED}
    ),
    EntityStatus.APPROVED: frozenset(),
    EntityStatus.REJECTED: frozenset(),
}


def validate_transition(current: EntityStatus, new: EntityStatus) -> None:
    if new not in VALID_TRANSITIONS[current]:
        raise InvalidTransitionError(
            f"Invalid state transition: {current.value} -> {new.value}"
        )
