"""Typed shared state flowing through the LangGraph workflow."""

import operator
from decimal import Decimal
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, Field

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
    # Generated per run by construction, so no entry point can produce
    # NULL-grouped audit rows.
    execution_id: str = Field(default_factory=lambda: str(uuid4()))
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

    # Human-in-the-loop: when true the graph pauses at PENDING_APPROVAL via
    # interrupt() instead of ending, and the approver's decision resumes it.
    hitl: bool = False

    # Outcome
    final_status: EntityStatus | None = None
    status_reason: str | None = None
    error: str | None = None

    #: How many audit rows the audit node wrote, so a resumed run can
    #: continue the step numbering instead of recomputing it.
    steps_persisted: int = 0

    audit_entries: Annotated[list[AuditEntry], operator.add] = []
