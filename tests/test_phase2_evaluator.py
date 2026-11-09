"""Phase 2: deterministic policy evaluator (D1-D3) against seeded data."""

from decimal import Decimal

import pytest

from app.models import ApprovalPolicy, Role, User
from app.models.enums import CapabilityType, EntityType
from app.schemas.approval import DecisionOutcome, PolicyInfo, RuleInfo
from app.services.evaluator import creator_has_authority, evaluate_policy
from app.tools import fetch_active_policies, get_approval_rules


def _evaluate(db, entity_type, capability, value):
    policies = fetch_active_policies(db, entity_type, capability)
    rules = {p.id: get_approval_rules(db, p.id) for p in policies}
    return evaluate_policy(policies, rules, Decimal(value))


def _role_ids(db, user_email) -> set[int]:
    user = db.query(User).filter_by(email=user_email).one()
    return {r.id for r in user.roles}


class TestBoundaries:
    """Exact D1 boundary values, both policies."""

    @pytest.mark.parametrize(
        ("value", "role"),
        [
            ("0.01", "SUPERVISOR"),
            ("9999.99", "SUPERVISOR"),
            ("10000.00", "DEPARTMENT_HEAD"),
            ("75000.00", "DEPARTMENT_HEAD"),
            ("100000.00", "DEPARTMENT_HEAD"),
            ("100000.01", "FINANCE_HEAD"),
            ("9999999999.99", "FINANCE_HEAD"),
        ],
    )
    def test_purchase_bands(self, seeded_db, value, role):
        decision = _evaluate(seeded_db, EntityType.ORDER, CapabilityType.PURCHASE, value)
        assert decision.outcome == DecisionOutcome.RULE_SELECTED
        assert decision.required_role_name == role

    @pytest.mark.parametrize(
        ("value", "role"),
        [
            ("24999.99", "SALES_EXECUTIVE"),
            ("25000.00", "SALES_EXECUTIVE"),  # sample-JSON interpretation (D1)
            ("25000.01", "SALES_MANAGER"),
            ("200000.00", "SALES_MANAGER"),
            ("200000.01", "BUSINESS_HEAD"),
        ],
    )
    def test_sales_bands(self, seeded_db, value, role):
        decision = _evaluate(seeded_db, EntityType.QUOTE, CapabilityType.SALES, value)
        assert decision.outcome == DecisionOutcome.RULE_SELECTED
        assert decision.required_role_name == role

    def test_admin_catchall_never_becomes_required_approver(self, seeded_db):
        decision = _evaluate(seeded_db, EntityType.ORDER, CapabilityType.PURCHASE, "75000")
        assert decision.required_role_name == "DEPARTMENT_HEAD"
        # ...but the Admin catch-all IS among the matching rules
        admin_id = seeded_db.query(Role).filter_by(name="ADMIN").one().id
        assert admin_id in decision.matching_role_ids

    def test_explanation_names_rule_and_policy(self, seeded_db):
        decision = _evaluate(seeded_db, EntityType.ORDER, CapabilityType.PURCHASE, "75000")
        assert f"rule {decision.required_rule_id}" in decision.explanation
        assert f"policy {decision.policy_id}" in decision.explanation


class TestCreatorAuthority:
    """D2: authority via ANY matching rule, no hardcoded roles."""

    def test_admin_authorized_at_any_value(self, seeded_db):
        roles = _role_ids(seeded_db, "alice.admin@example.com")
        for value in ("5.00", "75000.00", "9999999.99"):
            decision = _evaluate(seeded_db, EntityType.ORDER, CapabilityType.PURCHASE, value)
            assert creator_has_authority(decision, roles)

    def test_department_head_authorized_in_own_band(self, seeded_db):
        roles = _role_ids(seeded_db, "carol.depthead@example.com")
        decision = _evaluate(seeded_db, EntityType.ORDER, CapabilityType.PURCHASE, "75000")
        assert creator_has_authority(decision, roles)

    def test_supervisor_not_authorized_above_band(self, seeded_db):
        roles = _role_ids(seeded_db, "bob.supervisor@example.com")
        decision = _evaluate(seeded_db, EntityType.ORDER, CapabilityType.PURCHASE, "75000")
        assert not creator_has_authority(decision, roles)

    def test_strict_bands_no_downward_hierarchy(self, seeded_db):
        """D5: Finance Head is NOT authorized for a low-value order."""
        roles = _role_ids(seeded_db, "dave.financehead@example.com")
        decision = _evaluate(seeded_db, EntityType.ORDER, CapabilityType.PURCHASE, "5000")
        assert decision.required_role_name == "SUPERVISOR"
        assert not creator_has_authority(decision, roles)

    def test_no_authority_on_escalation_outcomes(self, seeded_db):
        roles = _role_ids(seeded_db, "alice.admin@example.com")
        decision = _evaluate(seeded_db, EntityType.ORDER, CapabilityType.SALES, "100")
        assert decision.outcome == DecisionOutcome.NO_ACTIVE_POLICY
        assert not creator_has_authority(decision, roles)


class TestConflictsAndEscalation:
    """D3: every ambiguity escalates, never an arbitrary pick."""

    def test_no_active_policy(self, seeded_db):
        policy = (
            seeded_db.query(ApprovalPolicy)
            .filter_by(entity_type=EntityType.ORDER)
            .one()
        )
        policy.is_active = False
        seeded_db.flush()
        decision = _evaluate(seeded_db, EntityType.ORDER, CapabilityType.PURCHASE, "5000")
        assert decision.outcome == DecisionOutcome.NO_ACTIVE_POLICY
        assert decision.is_escalation

    def test_policy_conflict_two_active(self, seeded_db):
        seeded_db.add(
            ApprovalPolicy(
                entity_type=EntityType.ORDER,
                capability=CapabilityType.PURCHASE,
                is_active=True,
            )
        )
        seeded_db.flush()
        decision = _evaluate(seeded_db, EntityType.ORDER, CapabilityType.PURCHASE, "5000")
        assert decision.outcome == DecisionOutcome.POLICY_CONFLICT
        assert decision.is_escalation

    def test_no_matching_rule(self):
        policy = PolicyInfo(
            id=1, entity_type=EntityType.ORDER, capability=CapabilityType.PURCHASE, is_active=True
        )
        rules = [
            RuleInfo(
                id=1, policy_id=1, min_value=Decimal("100"), max_value=Decimal("200"),
                allowed_role_id=1, allowed_role_name="SUPERVISOR", priority=10,
            )
        ]
        decision = evaluate_policy([policy], {1: rules}, Decimal("50"))
        assert decision.outcome == DecisionOutcome.NO_MATCHING_RULE
        assert decision.is_escalation

    def test_same_priority_overlap_is_conflict(self):
        policy = PolicyInfo(
            id=1, entity_type=EntityType.ORDER, capability=CapabilityType.PURCHASE, is_active=True
        )
        rules = [
            RuleInfo(id=1, policy_id=1, min_value=Decimal("0"), max_value=Decimal("1000"),
                     allowed_role_id=1, allowed_role_name="SUPERVISOR", priority=10),
            RuleInfo(id=2, policy_id=1, min_value=Decimal("500"), max_value=Decimal("2000"),
                     allowed_role_id=2, allowed_role_name="DEPARTMENT_HEAD", priority=10),
        ]
        decision = evaluate_policy([policy], {1: rules}, Decimal("700"))
        assert decision.outcome == DecisionOutcome.RULE_PRIORITY_CONFLICT
        assert decision.is_escalation

    def test_overlap_resolved_by_priority(self):
        """Overlapping rules at different priorities pick the higher precedence."""
        policy = PolicyInfo(
            id=1, entity_type=EntityType.ORDER, capability=CapabilityType.PURCHASE, is_active=True
        )
        rules = [
            RuleInfo(id=1, policy_id=1, min_value=Decimal("0"), max_value=Decimal("1000"),
                     allowed_role_id=1, allowed_role_name="SUPERVISOR", priority=10),
            RuleInfo(id=2, policy_id=1, min_value=Decimal("0"), max_value=None,
                     allowed_role_id=2, allowed_role_name="ADMIN", priority=100),
        ]
        decision = evaluate_policy([policy], {1: rules}, Decimal("700"))
        assert decision.outcome == DecisionOutcome.RULE_SELECTED
        assert decision.required_role_name == "SUPERVISOR"
        assert decision.matching_rule_ids == [1, 2]
