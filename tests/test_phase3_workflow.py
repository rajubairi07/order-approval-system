"""Phase 3: the shared LangGraph workflow end-to-end in mock-LLM mode.

Every test here runs the genuine agent tool-calling loop (MockChatModel)
with zero API keys — assignment test case 16 by construction.
"""

from decimal import Decimal

import pytest

from app.agents.orchestrator import process_entity
from app.llm.mock import MockChatModel
from app.models import ApprovalLog, ApprovalPolicy, Order, Quote, User
from app.models.enums import EntityStatus, EntityType


@pytest.fixture()
def llm():
    return MockChatModel()


def _user(db, email) -> User:
    return db.query(User).filter_by(email=email).one()


def _order(db, creator_email, value) -> Order:
    order = Order(created_by=_user(db, creator_email).id, order_value=Decimal(value))
    db.add(order)
    db.commit()
    return order


def _quote(db, creator_email, value) -> Quote:
    quote = Quote(created_by=_user(db, creator_email).id, quote_value=Decimal(value))
    db.add(quote)
    db.commit()
    return quote


def _logs(db, entity_type, entity_id) -> list[ApprovalLog]:
    return (
        db.query(ApprovalLog)
        .filter_by(entity_type=entity_type, entity_id=entity_id)
        .order_by(ApprovalLog.id)
        .all()
    )


class TestAutoApproval:
    def test_admin_order_any_value_auto_approved(self, seeded_db, llm):
        order = _order(seeded_db, "alice.admin@example.com", "999999.99")
        result = process_entity(seeded_db, EntityType.ORDER, order.id, llm)
        assert result.final_status == EntityStatus.APPROVED
        assert result.creator_authorized is True
        seeded_db.refresh(order)
        assert order.status == EntityStatus.APPROVED
        assert order.current_approver is None

    def test_supervisor_low_value_auto_approved(self, seeded_db, llm):
        order = _order(seeded_db, "bob.supervisor@example.com", "5000.00")
        result = process_entity(seeded_db, EntityType.ORDER, order.id, llm)
        assert result.final_status == EntityStatus.APPROVED

    def test_dual_capability_user_both_flows(self, seeded_db, llm):
        """Hema (SUPERVISOR + SALES_EXECUTIVE, both capabilities)."""
        order = _order(seeded_db, "hema.dual@example.com", "4000.00")
        quote = _quote(seeded_db, "hema.dual@example.com", "20000.00")
        assert (
            process_entity(seeded_db, EntityType.ORDER, order.id, llm).final_status
            == EntityStatus.APPROVED
        )
        assert (
            process_entity(seeded_db, EntityType.QUOTE, quote.id, llm).final_status
            == EntityStatus.APPROVED
        )


class TestRoutingToApprover:
    def test_supervisor_high_value_routed_to_department_head(self, seeded_db, llm):
        order = _order(seeded_db, "bob.supervisor@example.com", "75000.00")
        result = process_entity(seeded_db, EntityType.ORDER, order.id, llm)
        assert result.final_status == EntityStatus.PENDING_APPROVAL
        carol = _user(seeded_db, "carol.depthead@example.com")
        assert result.approver_id == carol.id
        seeded_db.refresh(order)
        assert order.status == EntityStatus.PENDING_APPROVAL
        assert order.current_approver == carol.id

    def test_sales_exec_high_value_quote_routed(self, seeded_db, llm):
        quote = _quote(seeded_db, "erin.salesexec@example.com", "300000.00")
        result = process_entity(seeded_db, EntityType.QUOTE, quote.id, llm)
        assert result.final_status == EntityStatus.PENDING_APPROVAL
        grace = _user(seeded_db, "grace.bizhead@example.com")
        assert result.approver_id == grace.id

    def test_boundary_25000_stays_with_sales_executive(self, seeded_db, llm):
        """D1: sample-JSON interpretation of the assignment's inconsistency."""
        quote = _quote(seeded_db, "erin.salesexec@example.com", "25000.00")
        result = process_entity(seeded_db, EntityType.QUOTE, quote.id, llm)
        assert result.final_status == EntityStatus.APPROVED  # Erin is in band

    def test_boundary_25000_01_requires_sales_manager(self, seeded_db, llm):
        quote = _quote(seeded_db, "erin.salesexec@example.com", "25000.01")
        result = process_entity(seeded_db, EntityType.QUOTE, quote.id, llm)
        assert result.final_status == EntityStatus.PENDING_APPROVAL
        assert result.decision.required_role_name == "SALES_MANAGER"


class TestRejectionAndEscalation:
    def test_missing_capability_rejected_inside_graph(self, seeded_db, llm):
        """D4: Judy holds SUPERVISOR but no PURCHASE capability."""
        order = _order(seeded_db, "judy.nocap@example.com", "500.00")
        result = process_entity(seeded_db, EntityType.ORDER, order.id, llm)
        assert result.final_status == EntityStatus.REJECTED
        assert result.capability_ok is False
        assert "validation failure" in result.status_reason
        seeded_db.refresh(order)
        assert order.status == EntityStatus.REJECTED

    def test_no_eligible_approver_escalates(self, seeded_db, llm):
        carol = _user(seeded_db, "carol.depthead@example.com")
        carol.is_active = False
        seeded_db.commit()
        order = _order(seeded_db, "bob.supervisor@example.com", "75000.00")
        result = process_entity(seeded_db, EntityType.ORDER, order.id, llm)
        assert result.final_status == EntityStatus.ESCALATED
        assert result.approver_id is None

    def test_inactive_policy_escalates(self, seeded_db, llm):
        policy = (
            seeded_db.query(ApprovalPolicy).filter_by(entity_type=EntityType.ORDER).one()
        )
        policy.is_active = False
        seeded_db.commit()
        order = _order(seeded_db, "bob.supervisor@example.com", "500.00")
        result = process_entity(seeded_db, EntityType.ORDER, order.id, llm)
        assert result.final_status == EntityStatus.ESCALATED
        assert result.decision.outcome.value == "NO_ACTIVE_POLICY"

    def test_policy_conflict_escalates(self, seeded_db, llm):
        from app.models.enums import CapabilityType

        seeded_db.add(
            ApprovalPolicy(
                entity_type=EntityType.ORDER,
                capability=CapabilityType.PURCHASE,
                is_active=True,
            )
        )
        seeded_db.commit()
        order = _order(seeded_db, "alice.admin@example.com", "500.00")
        result = process_entity(seeded_db, EntityType.ORDER, order.id, llm)
        assert result.final_status == EntityStatus.ESCALATED
        assert result.decision.outcome.value == "POLICY_CONFLICT"


class TestAuditTrail:
    def test_per_step_logs_with_policy_and_rule_ids(self, seeded_db, llm):
        order = _order(seeded_db, "bob.supervisor@example.com", "75000.00")
        result = process_entity(seeded_db, EntityType.ORDER, order.id, llm)
        logs = _logs(seeded_db, EntityType.ORDER, order.id)

        actions = [log.action for log in logs]
        assert actions == [
            "validate_capability",
            "evaluate_policy",
            "authority_check",
            "route_for_approval",
            "final_decision",
        ]
        policy_log = next(l for l in logs if l.action == "evaluate_policy")
        assert policy_log.policy_id == result.decision.policy_id
        assert policy_log.rule_applied == result.decision.required_rule_id
        assert policy_log.agent_name == "PolicyEvaluationAgent"
        assert "fetch_active_policies" in policy_log.tool_called
        assert "DEPARTMENT_HEAD" in policy_log.reasoning_trace

        final = logs[-1]
        assert final.agent_name == "AuditExplainabilityAgent"
        assert final.rule_applied == result.decision.required_rule_id
        assert "PENDING_APPROVAL" in final.reasoning_trace

    def test_no_guardrail_overrides_in_mock_mode(self, seeded_db, llm):
        """Mock submissions must match deterministic truth exactly."""
        order = _order(seeded_db, "bob.supervisor@example.com", "75000.00")
        process_entity(seeded_db, EntityType.ORDER, order.id, llm)
        logs = _logs(seeded_db, EntityType.ORDER, order.id)
        assert not any(l.remarks and "Guardrail" in l.remarks for l in logs)

    def test_tool_traces_recorded(self, seeded_db, llm):
        order = _order(seeded_db, "alice.admin@example.com", "100.00")
        process_entity(seeded_db, EntityType.ORDER, order.id, llm)
        logs = _logs(seeded_db, EntityType.ORDER, order.id)
        context_log = next(l for l in logs if l.action == "validate_capability")
        for tool in ("get_entity_details", "get_user_roles", "get_user_capabilities"):
            assert tool in context_log.tool_called
