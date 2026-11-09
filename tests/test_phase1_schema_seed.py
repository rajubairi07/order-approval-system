"""Phase 1 verification: schema constraints and seed-data integrity."""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.database.seed import seed
from app.models import (
    ApprovalPolicy,
    ApprovalRule,
    Capability,
    CapabilityType,
    EntityStatus,
    EntityType,
    Order,
    Role,
    User,
)


class TestSeedIntegrity:
    def test_roles_and_capabilities(self, seeded_db):
        assert seeded_db.query(Role).count() == 7
        assert {c.name for c in seeded_db.query(Capability)} == {
            CapabilityType.PURCHASE,
            CapabilityType.SALES,
        }

    def test_seed_is_idempotent(self, seeded_db):
        seed(seeded_db)  # second run must not duplicate anything
        assert seeded_db.query(Role).count() == 7
        assert seeded_db.query(User).count() == 10
        assert seeded_db.query(ApprovalPolicy).count() == 2

    def test_dual_capability_user(self, seeded_db):
        hema = seeded_db.query(User).filter_by(email="hema.dual@example.com").one()
        assert {c.name for c in hema.capabilities} == {
            CapabilityType.PURCHASE,
            CapabilityType.SALES,
        }
        assert {r.name for r in hema.roles} == {"SUPERVISOR", "SALES_EXECUTIVE"}

    def test_user_without_capability_exists(self, seeded_db):
        judy = seeded_db.query(User).filter_by(email="judy.nocap@example.com").one()
        assert judy.capabilities == []

    def test_inactive_approver_exists(self, seeded_db):
        ivan = seeded_db.query(User).filter_by(email="ivan.inactive@example.com").one()
        assert ivan.is_active is False


class TestSeedRuleBands:
    """The D1 boundary values must be exactly as specified."""

    def _rules(self, db, entity_type):
        policy = (
            db.query(ApprovalPolicy).filter_by(entity_type=entity_type, is_active=True).one()
        )
        return {
            (r.allowed_role.name): (r.min_value, r.max_value, r.priority)
            for r in policy.rules
        }

    def test_purchase_bands(self, seeded_db):
        rules = self._rules(seeded_db, EntityType.ORDER)
        assert rules["SUPERVISOR"] == (Decimal("0.00"), Decimal("9999.99"), 10)
        assert rules["DEPARTMENT_HEAD"] == (Decimal("10000.00"), Decimal("100000.00"), 10)
        assert rules["FINANCE_HEAD"] == (Decimal("100000.01"), None, 10)
        assert rules["ADMIN"] == (Decimal("0.00"), None, 100)

    def test_sales_bands(self, seeded_db):
        rules = self._rules(seeded_db, EntityType.QUOTE)
        assert rules["SALES_EXECUTIVE"] == (Decimal("0.00"), Decimal("25000.00"), 10)
        assert rules["SALES_MANAGER"] == (Decimal("25000.01"), Decimal("200000.00"), 10)
        assert rules["BUSINESS_HEAD"] == (Decimal("200000.01"), None, 10)
        assert rules["ADMIN"] == (Decimal("0.00"), None, 100)

    def test_bands_leave_no_2dp_gaps(self, seeded_db):
        """At 2-decimal granularity every value >= 0 must match >= 1 rule."""
        for entity_type in (EntityType.ORDER, EntityType.QUOTE):
            policy = (
                seeded_db.query(ApprovalPolicy)
                .filter_by(entity_type=entity_type, is_active=True)
                .one()
            )
            bands = sorted(
                [(r.min_value, r.max_value) for r in policy.rules if r.priority == 10]
            )
            assert bands[0][0] == Decimal("0.00")
            for (_, prev_max), (next_min, _) in zip(bands, bands[1:]):
                assert next_min - prev_max == Decimal("0.01")
            assert bands[-1][1] is None


class TestConstraints:
    def test_duplicate_email_rejected(self, seeded_db):
        seeded_db.add(User(name="Dup", email="alice.admin@example.com"))
        with pytest.raises(IntegrityError):
            seeded_db.commit()
        seeded_db.rollback()

    def test_duplicate_role_name_rejected(self, seeded_db):
        seeded_db.add(Role(name="ADMIN"))
        with pytest.raises(IntegrityError):
            seeded_db.commit()
        seeded_db.rollback()

    def test_rule_max_below_min_rejected(self, seeded_db):
        role = seeded_db.query(Role).filter_by(name="ADMIN").one()
        policy = seeded_db.query(ApprovalPolicy).first()
        seeded_db.add(
            ApprovalRule(
                policy_id=policy.id,
                min_value=Decimal("100"),
                max_value=Decimal("50"),
                allowed_role_id=role.id,
            )
        )
        with pytest.raises(IntegrityError):
            seeded_db.commit()
        seeded_db.rollback()

    def test_negative_order_value_rejected(self, seeded_db):
        creator = seeded_db.query(User).filter_by(email="bob.supervisor@example.com").one()
        seeded_db.add(Order(created_by=creator.id, order_value=Decimal("-5")))
        with pytest.raises(IntegrityError):
            seeded_db.commit()
        seeded_db.rollback()

    def test_order_defaults(self, seeded_db):
        creator = seeded_db.query(User).filter_by(email="bob.supervisor@example.com").one()
        order = Order(created_by=creator.id, order_value=Decimal("5000.00"))
        seeded_db.add(order)
        seeded_db.commit()
        assert order.status == EntityStatus.CREATED
        assert order.current_approver is None
        assert order.order_value == Decimal("5000.00")
