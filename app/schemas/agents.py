"""Structured outputs each agent must submit (spec: validated handoffs)."""

from pydantic import BaseModel, Field


class ContextResult(BaseModel):
    """Submitted by the Context & Capability Agent."""

    creator_active: bool = Field(description="Whether the creator account is active")
    creator_role_ids: list[int] = Field(description="Role ids held by the creator")
    creator_role_names: list[str] = Field(description="Role names held by the creator")
    creator_capabilities: list[str] = Field(
        description="Capability names (PURCHASE/SALES) held by the creator"
    )
    capability_ok: bool = Field(
        description="True iff the creator is active and holds the required capability"
    )
    explanation: str = Field(description="One-sentence explanation of the validation")


class RoutingResult(BaseModel):
    """Submitted by the Routing & Workflow Agent."""

    approver_id: int | None = Field(
        description="Chosen eligible approver's user id, or null when none exists"
    )
    explanation: str = Field(description="One-sentence explanation of the routing")
