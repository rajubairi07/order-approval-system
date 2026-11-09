"""Request/response schemas for Policies and Audit."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import CapabilityType, EntityType


class RuleCreate(BaseModel):
    min_value: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    max_value: Decimal | None = Field(
        default=None, max_digits=12, decimal_places=2,
        description="Inclusive upper bound; null = unlimited",
    )
    allowed_role_id: int
    priority: int = Field(default=10, ge=0, description="Lower number = higher precedence")

    @model_validator(mode="after")
    def _max_gte_min(self):
        if self.max_value is not None and self.max_value < self.min_value:
            raise ValueError("max_value must be >= min_value")
        return self


class PolicyCreate(BaseModel):
    entity_type: EntityType
    capability: CapabilityType
    is_active: bool = True
    rules: list[RuleCreate] = Field(min_length=1)


class RuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    min_value: Decimal
    max_value: Decimal | None
    allowed_role_id: int
    priority: int


class PolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_type: EntityType
    capability: CapabilityType
    is_active: bool
    rules: list[RuleResponse]


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_type: EntityType
    entity_id: int
    action: str
    performed_by: int | None
    agent_name: str | None
    policy_id: int | None
    rule_applied: int | None
    tool_called: str | None
    reasoning_trace: str | None
    timestamp: datetime
    remarks: str | None
