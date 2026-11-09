"""Shared router factory for Orders and Quotes.

Both resources run the same workflow and action service (assignment: one
reusable approval engine); only the value field name and schemas differ.
"""

from decimal import Decimal
from typing import Callable

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import tools as db_tools
from app.database.connection import get_db
from app.models.enums import EntityType
from app.schemas.approval import EntityInfo
from app.schemas.order import ActionRequest
from app.services import entity_service


def build_entity_router(
    *,
    prefix: str,
    entity_type: EntityType,
    create_schema: type,
    response_schema: type,
    value_field: str,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[prefix.strip("/")])

    def _respond(info: EntityInfo, status_reason: str | None = None):
        return response_schema(
            id=info.id,
            created_by=info.created_by,
            status=info.status,
            current_approver=info.current_approver,
            status_reason=status_reason,
            **{value_field: info.value},
        )

    @router.post("", response_model=response_schema, status_code=201)
    def create(payload: create_schema, db: Session = Depends(get_db)):  # type: ignore[valid-type]
        """Create the entity and run the multi-agent approval workflow on it."""
        value: Decimal = getattr(payload, value_field)
        info, state = entity_service.create_and_process(
            db, entity_type, payload.created_by, value
        )
        return _respond(info, state.status_reason)

    @router.get("/{entity_id}", response_model=response_schema)
    def read(entity_id: int, db: Session = Depends(get_db)):
        return _respond(db_tools.get_entity_details(db, entity_type, entity_id))

    @router.post("/{entity_id}/action", response_model=response_schema)
    def action(entity_id: int, payload: ActionRequest, db: Session = Depends(get_db)):
        """Submit a human APPROVE/REJECT decision (assigned approver only)."""
        info = entity_service.apply_action(
            db, entity_type, entity_id, payload.user_id, payload.action, payload.remarks
        )
        return _respond(info)

    return router
