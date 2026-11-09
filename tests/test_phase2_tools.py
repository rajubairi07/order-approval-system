"""Phase 2: database tools and state machine."""

from decimal import Decimal

import pytest

from app.exceptions import InvalidTransitionError, NotFoundError
from app.models import ApprovalLog, Order, Role, User
from app.models.enums import CapabilityType, EntityStatus, EntityType
from app.services.state_machine import validate_transition
from app.tools import (
    assign_approver,
    find_eligible_approvers,
    get_entity_details,
    get_user_capabilities,
    get_user_details,
    get_user_roles,
    persist_audit_log,
    update_entity_status,
)


def _user(db, email) -> User:
    return db.query(User).filter_by(email=email).one()


def _make_order(db, creator_email="bob.supervisor@example.com", value="5000.00") -> Order:
    order = Order(created_by=_user(db, creator_email).id, order_value=Decimal(value))
    db.add(order)
    db.flush()
    return order


class TestUserAndEntityTools:
    def test_get_user_details_roles_capabilities(self, seeded_db):
        hema = _user(seeded_db, "hema.dual@example.com")
        assert get_user_details(seeded_db, hema.id).is_active is True
        assert {r.name for r in get_user_roles(seeded_db, hema.id)} == {
            "SUPERVISOR",
            "SALES_EXECUTIVE",
        }
        assert set(get_user_capabilities(seeded_db, hema.id)) == {"PURCHASE", "SALES"}

    def test_missing_user_raises(self, seeded_db):
        with pytest.raises(NotFoundError):
            get_user_details(seeded_db, 99999)

    def test_get_entity_details_uniform_view(self, seeded_db):
        order = _make_order(seeded_db)
        info = get_entity_details(seeded_db, EntityType.ORDER, order.id)
        assert info.value == Decimal("5000.00")
        assert info.status == EntityStatus.CREATED
        assert info.entity_type == EntityType.ORDER

    def test_missing_entity_raises(self, seeded_db):
        with pytest.raises(NotFoundError):
            get_entity_details(seeded_db, EntityType.QUOTE, 99999)

    def test_assign_approver(self, seeded_db):
        order = _make_order(seeded_db)
        carol = _user(seeded_db, "carol.depthead@example.com")
        info = assign_approver(seeded_db, EntityType.ORDER, order.id, carol.id)
        assert info.current_approver == carol.id


class TestStateMachine:
    @pytest.mark.parametrize(
        ("current", "new"),
        [
            (EntityStatus.CREATED, EntityStatus.APPROVED),
            (EntityStatus.CREATED, EntityStatus.PENDING_APPROVAL),
            (EntityStatus.CREATED, EntityStatus.REJECTED),
            (EntityStatus.CREATED, EntityStatus.ESCALATED),
            (EntityStatus.PENDING_APPROVAL, EntityStatus.APPROVED),
            (EntityStatus.PENDING_APPROVAL, EntityStatus.REJECTED),
            (EntityStatus.ESCALATED, EntityStatus.APPROVED),
            (EntityStatus.ESCALATED, EntityStatus.REJECTED),
        ],
    )
    def test_valid_transitions(self, current, new):
        validate_transition(current, new)  # must not raise

    @pytest.mark.parametrize(
        ("current", "new"),
        [
            (EntityStatus.APPROVED, EntityStatus.REJECTED),
            (EntityStatus.REJECTED, EntityStatus.APPROVED),
            (EntityStatus.APPROVED, EntityStatus.PENDING_APPROVAL),
            (EntityStatus.PENDING_APPROVAL, EntityStatus.CREATED),
            (EntityStatus.PENDING_APPROVAL, EntityStatus.ESCALATED),
            (EntityStatus.ESCALATED, EntityStatus.PENDING_APPROVAL),
        ],
    )
    def test_invalid_transitions(self, current, new):
        with pytest.raises(InvalidTransitionError):
            validate_transition(current, new)

    def test_update_entity_status_enforces_machine(self, seeded_db):
        order = _make_order(seeded_db)
        info = update_entity_status(
            seeded_db, EntityType.ORDER, order.id, EntityStatus.APPROVED
        )
        assert info.status == EntityStatus.APPROVED
        with pytest.raises(InvalidTransitionError):
            update_entity_status(
                seeded_db, EntityType.ORDER, order.id, EntityStatus.REJECTED
            )


class TestApproverDiscovery:
    def _role_id(self, db, name) -> int:
        return db.query(Role).filter_by(name=name).one().id

    def test_finds_active_capable_holder(self, seeded_db):
        role_id = self._role_id(seeded_db, "DEPARTMENT_HEAD")
        approvers = find_eligible_approvers(seeded_db, role_id, CapabilityType.PURCHASE)
        emails = {a.email for a in approvers}
        assert "carol.depthead@example.com" in emails
        # Ivan holds the role but is inactive — must be excluded
        assert "ivan.inactive@example.com" not in emails

    def test_capability_required(self, seeded_db):
        """Judy holds SUPERVISOR but has no PURCHASE capability."""
        role_id = self._role_id(seeded_db, "SUPERVISOR")
        approvers = find_eligible_approvers(seeded_db, role_id, CapabilityType.PURCHASE)
        emails = {a.email for a in approvers}
        assert "judy.nocap@example.com" not in emails
        assert "bob.supervisor@example.com" in emails

    def test_creator_excluded_and_deterministic_order(self, seeded_db):
        role_id = self._role_id(seeded_db, "SUPERVISOR")
        bob = _user(seeded_db, "bob.supervisor@example.com")
        approvers = find_eligible_approvers(
            seeded_db, role_id, CapabilityType.PURCHASE, exclude_user_id=bob.id
        )
        assert all(a.id != bob.id for a in approvers)
        assert [a.id for a in approvers] == sorted(a.id for a in approvers)

    def test_no_eligible_approver(self, seeded_db):
        """Deactivate the only capable Department Head -> empty result."""
        carol = _user(seeded_db, "carol.depthead@example.com")
        carol.is_active = False
        seeded_db.flush()
        role_id = self._role_id(seeded_db, "DEPARTMENT_HEAD")
        assert find_eligible_approvers(seeded_db, role_id, CapabilityType.PURCHASE) == []


class TestAuditTool:
    def test_persist_audit_log(self, seeded_db):
        order = _make_order(seeded_db)
        log_id = persist_audit_log(
            seeded_db,
            EntityType.ORDER,
            order.id,
            action="evaluate_policy",
            agent_name="PolicyEvaluationAgent",
            policy_id=1,
            rule_applied=2,
            tool_called="fetch_active_policies,get_approval_rules",
            reasoning_trace="Value 5000.00 falls within the SUPERVISOR band.",
        )
        seeded_db.commit()
        log = seeded_db.get(ApprovalLog, log_id)
        assert log.agent_name == "PolicyEvaluationAgent"
        assert log.rule_applied == 2
        assert log.policy_id == 1
        assert log.timestamp is not None
