from decimal import Decimal

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import EntityStatus
from app.models.user import User


class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    quote_value: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[EntityStatus] = mapped_column(
        Enum(EntityStatus, native_enum=False, length=20),
        default=EntityStatus.CREATED,
        index=True,
    )
    current_approver: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    creator: Mapped[User] = relationship(foreign_keys=[created_by])
    approver: Mapped[User | None] = relationship(foreign_keys=[current_approver])

    __table_args__ = (
        CheckConstraint("quote_value > 0", name="value_positive"),
    )
