"""Request/response schemas for Quotes."""

from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import EntityStatus
from app.schemas.order import Money


class QuoteCreate(BaseModel):
    created_by: int = Field(description="User id of the creating employee")
    quote_value: Decimal = Money


class QuoteResponse(BaseModel):
    id: int
    created_by: int
    quote_value: Decimal
    status: EntityStatus
    current_approver: int | None
    status_reason: str | None = Field(
        default=None, description="Workflow explanation (POST responses only)"
    )
