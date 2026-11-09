from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, Index, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import CapabilityType, EntityType


class ApprovalPolicy(Base):
    """A set of approval rules for one entity type + capability.

    Deliberately NO unique constraint on (entity_type, capability, is_active):
    two simultaneously active policies are a *policy conflict*, which the
    workflow must detect and resolve by escalating (spec decision D3).
    """

    __tablename__ = "approval_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[EntityType] = mapped_column(
        Enum(EntityType, native_enum=False, length=20)
    )
    capability: Mapped[CapabilityType] = mapped_column(
        Enum(CapabilityType, native_enum=False, length=20)
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    rules: Mapped[list["ApprovalRule"]] = relationship(
        back_populates="policy", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_approval_policies_lookup", "entity_type", "capability", "is_active"),
    )


class ApprovalRule(Base):
    """One value band within a policy.

    Interval semantics (spec decision D1): [min_value, max_value] both ends
    inclusive at 2-decimal granularity; max_value NULL means unbounded.

    Priority (spec decision D2): lower number = higher precedence. Band rules
    are seeded at 10; the Admin catch-all at 100, so it never determines the
    required approver but still grants creators matching it auto-approve
    authority.
    """

    __tablename__ = "approval_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("approval_policies.id", ondelete="CASCADE"), index=True
    )
    min_value: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    max_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    allowed_role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    priority: Mapped[int] = mapped_column(default=10)

    policy: Mapped[ApprovalPolicy] = relationship(back_populates="rules")
    allowed_role: Mapped["Role"] = relationship()  # noqa: F821

    __table_args__ = (
        CheckConstraint("min_value >= 0", name="min_value_non_negative"),
        CheckConstraint(
            "max_value IS NULL OR max_value >= min_value", name="max_gte_min"
        ),
        CheckConstraint("priority >= 0", name="priority_non_negative"),
    )
