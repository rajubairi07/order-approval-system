"""Pydantic models for tool outputs and inter-agent decisions.

Every piece of authoritative data an agent handles flows through these
validated schemas — never free-form LLM text (spec decision D9).
"""

import enum
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.enums import CapabilityType, EntityStatus, EntityType


class RoleInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class UserInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    is_active: bool


class EntityInfo(BaseModel):
    """Uniform view over Order and Quote rows."""

    entity_type: EntityType
    id: int
    created_by: int
    value: Decimal
    status: EntityStatus
    current_approver: int | None


class RuleInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    policy_id: int
    min_value: Decimal
    max_value: Decimal | None
    allowed_role_id: int
    allowed_role_name: str
    priority: int


class PolicyInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_type: EntityType
    capability: CapabilityType
    is_active: bool


class DecisionOutcome(str, enum.Enum):
    RULE_SELECTED = "RULE_SELECTED"
    NO_ACTIVE_POLICY = "NO_ACTIVE_POLICY"
    POLICY_CONFLICT = "POLICY_CONFLICT"
    NO_MATCHING_RULE = "NO_MATCHING_RULE"
    RULE_PRIORITY_CONFLICT = "RULE_PRIORITY_CONFLICT"


#: Outcomes that the workflow resolves by escalating (spec decision D3).
ESCALATION_OUTCOMES = frozenset(
    {
        DecisionOutcome.NO_ACTIVE_POLICY,
        DecisionOutcome.POLICY_CONFLICT,
        DecisionOutcome.NO_MATCHING_RULE,
        DecisionOutcome.RULE_PRIORITY_CONFLICT,
    }
)


class PolicyDecision(BaseModel):
    """Validated result of deterministic policy evaluation (spec D2/D3).

    - required_* identify the highest-precedence matching rule: the role an
      external approver must hold.
    - matching_rule_ids/matching_role_ids cover ALL matching rules: a creator
      holding any of these roles is authorized to auto-approve.
    """

    outcome: DecisionOutcome
    policy_id: int | None = None
    required_rule_id: int | None = None
    required_role_id: int | None = None
    required_role_name: str | None = None
    matching_rule_ids: list[int] = []
    matching_role_ids: list[int] = []
    explanation: str

    @property
    def is_escalation(self) -> bool:
        return self.outcome in ESCALATION_OUTCOMES
