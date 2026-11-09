from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models import ApprovalLog
from app.models.enums import EntityType
from app.schemas.policy import AuditLogResponse

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/{entity_type}/{entity_id}", response_model=list[AuditLogResponse])
def get_audit_trail(entity_type: EntityType, entity_id: int, db: Session = Depends(get_db)):
    """Step-by-step agent decisions and reasoning traces for one entity."""
    return (
        db.query(ApprovalLog)
        .filter_by(entity_type=entity_type, entity_id=entity_id)
        .order_by(ApprovalLog.id)
        .all()
    )
