"""Tool for discovering eligible approvers."""

from sqlalchemy.orm import Session

from app.models import Capability, Role, User, UserCapability, UserRole
from app.models.enums import CapabilityType
from app.schemas.approval import UserInfo


def find_eligible_approvers(
    db: Session,
    role_id: int,
    capability: CapabilityType,
    exclude_user_id: int | None = None,
) -> list[UserInfo]:
    """Active users holding the required role AND capability.

    The creator is excluded (no self-approval) and results are ordered by
    user id so approver selection is deterministic (spec decision D7).
    """
    query = (
        db.query(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(UserCapability, UserCapability.user_id == User.id)
        .join(Capability, Capability.id == UserCapability.capability_id)
        .filter(
            User.is_active.is_(True),
            UserRole.role_id == role_id,
            Capability.name == capability,
        )
        .order_by(User.id)
    )
    if exclude_user_id is not None:
        query = query.filter(User.id != exclude_user_id)
    return [UserInfo.model_validate(u) for u in query.all()]
