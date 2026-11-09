"""Tools for reading users, their roles, and their capabilities."""

from sqlalchemy.orm import Session

from app.exceptions import NotFoundError
from app.models import User
from app.schemas.approval import RoleInfo, UserInfo


def _load_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError(f"User {user_id} not found")
    return user


def get_user_details(db: Session, user_id: int) -> UserInfo:
    """Fetch a user's identity and active flag."""
    return UserInfo.model_validate(_load_user(db, user_id))


def get_user_roles(db: Session, user_id: int) -> list[RoleInfo]:
    """Fetch all roles assigned to a user."""
    return [RoleInfo.model_validate(r) for r in _load_user(db, user_id).roles]


def get_user_capabilities(db: Session, user_id: int) -> list[str]:
    """Fetch capability names (PURCHASE/SALES) assigned to a user."""
    return [c.name.value for c in _load_user(db, user_id).capabilities]
