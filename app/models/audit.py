from datetime import datetime, timezone

from sqlalchemy import Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import EntityType


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalLog(Base):
    """One row per agent/user action on an entity (spec decision D6).

    Extends the assignment schema with policy_id and tool_called: the audit
    requirement demands the exact policy applied and the tools invoked, which
    rule_applied alone cannot convey.
    """

    __tablename__ = "approval_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[EntityType] = mapped_column(
        Enum(EntityType, native_enum=False, length=20)
    )
    entity_id: Mapped[int] = mapped_column()
    action: Mapped[str] = mapped_column(String(100))
    performed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    agent_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    policy_id: Mapped[int | None] = mapped_column(
        ForeignKey("approval_policies.id"), nullable=True
    )
    rule_applied: Mapped[int | None] = mapped_column(
        ForeignKey("approval_rules.id"), nullable=True
    )
    tool_called: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reasoning_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(default=_utcnow)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_approval_logs_entity", "entity_type", "entity_id"),
    )
