from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models import ApprovalPolicy, ApprovalRule, Role
from app.schemas.policy import PolicyCreate, PolicyResponse

router = APIRouter(prefix="/policies", tags=["policies"])


@router.get("", response_model=list[PolicyResponse])
def list_policies(db: Session = Depends(get_db)):
    return db.query(ApprovalPolicy).order_by(ApprovalPolicy.id).all()


@router.post("", response_model=PolicyResponse, status_code=201)
def create_policy(payload: PolicyCreate, db: Session = Depends(get_db)):
    """Create a policy with its rules.

    A second ACTIVE policy for the same entity type + capability is allowed by
    design: the workflow detects it as a policy conflict and escalates (D3).
    """
    role_ids = {r.allowed_role_id for r in payload.rules}
    known = {r.id for r in db.query(Role).filter(Role.id.in_(role_ids))}
    missing = role_ids - known
    if missing:
        raise HTTPException(status_code=422, detail=f"Unknown role ids: {sorted(missing)}")

    policy = ApprovalPolicy(
        entity_type=payload.entity_type,
        capability=payload.capability,
        is_active=payload.is_active,
    )
    policy.rules = [ApprovalRule(**rule.model_dump()) for rule in payload.rules]
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy
