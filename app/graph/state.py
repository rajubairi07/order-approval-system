"""Typed shared state flowing through the LangGraph workflow."""

import operator
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel

from app.models.enums import CapabilityType, EntityStatus, EntityType
from app.schemas.approval import PolicyDecision


class AuditEntry(BaseModel):
    """One pending audit row, persisted by the audit node (spec D6)."""

    action: str
    agent_name: str | None = None
    policy_id: int | None = None
    rule_applied: int | None = None
    tool_called: str | None = None
    reasoning_trace: str | None = None
    remarks: str | None = None


class ApprovalState(BaseModel):
    """Shared LangGraph state; audit_entries accumulate across nodes."""

    # Workflow inputs (spec section 5)
    entity_type: EntityType
    entity_id: int
    creator_id: int
    entity_value: Decimal
    capability: CapabilityType

    # Context & Capability Agent results
    creator_active: bool | None = None
    creator_role_ids: list[int] = []
    creator_role_names: list[str] = []
    creator_capabilities: list[str] = []
    capability_ok: bool | None = None

    # Policy Evaluation Agent result
    decision: PolicyDecision | None = None

    # Deterministic authority check
    creator_authorized: bool | None = None

    # Routing & Workflow Agent result
    approver_id: int | None = None

    # Outcome
    final_status: EntityStatus | None = None
    status_reason: str | None = None
    error: str | None = None

    audit_entries: Annotated[list[AuditEntry], operator.add] = []
