"""Request/response schemas for Orders (and the shared action request)."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import EntityStatus

Money = Field(gt=0, max_digits=12, decimal_places=2)


class OrderCreate(BaseModel):
    created_by: int = Field(description="User id of the creating employee")
    order_value: Decimal = Money


class OrderResponse(BaseModel):
    id: int
    created_by: int
    order_value: Decimal
    status: EntityStatus
    current_approver: int | None
    status_reason: str | None = Field(
        default=None, description="Workflow explanation (POST responses only)"
    )


class ActionRequest(BaseModel):
    """Human approval decision (D7: identity via payload — no auth system)."""

    user_id: int = Field(description="Acting user; must be the assigned approver")
    action: Literal["APPROVE", "REJECT"]
    remarks: str | None = None
