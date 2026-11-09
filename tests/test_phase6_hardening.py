"""Phase 6: regression tests for the max-effort review findings."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient  # noqa: F401 (fixture reuse)
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agents.orchestrator import process_entity
from app.exceptions import InvalidActionError
from app.llm.mock import MockChatModel
from app.models import ApprovalLog, ApprovalPolicy, Order, User
from app.models.enums import CapabilityType, EntityStatus, EntityType
from app.services.entity_service import apply_action

from tests.test_phase4_api import client, _uid  # noqa: F401


def _deactivate(db, *emails):
    for email in emails:
        db.query(User).filter_by(email=email).one().is_active = False
    db.commit()


class TestCreatedEntityProtection:
    """A CREATED entity belongs to the workflow — no human action allowed."""

    def test_created_entity_action_is_409_for_anyone(self, client, seeded_db):
        # Simulate a workflow failure: row committed, graph never finished.
        bob = _uid(seeded_db, "bob.supervisor@example.com")
        order = Order(created_by=bob, order_value=Decimal("75000.00"))
        seeded_db.add(order)
        seeded_db.commit()

        for email in ("judy.nocap@example.com", "alice.admin@example.com"):
            resp = client.post(
                f"/orders/{order.id}/action",
                json={"user_id": _uid(seeded_db, email), "action": "APPROVE"},
            )
            assert resp.status_code == 409, email
        seeded_db.refresh(order)
        assert order.status == EntityStatus.CREATED


class TestEscalationAuthority:
    """D8 mirrors routing eligibility: capability required, no self-approval."""

    def test_creator_cannot_resolve_own_escalation(self, client, seeded_db):
        # Second active SALES policy -> POLICY_CONFLICT escalation
        seeded_db.add(
            ApprovalPolicy(
                entity_type=EntityType.QUOTE, capability=CapabilityType.SALES, is_active=True
            )
        )
        seeded_db.commit()
        erin = _uid(seeded_db, "erin.salesexec@example.com")
        created = client.post(
            "/quotes", json={"created_by": erin, "quote_value": "10000.00"}
        ).json()
        assert created["status"] == "ESCALATED"

        # Erin holds a matching role (SALES_EXECUTIVE band) but is the creator
        denied = client.post(
            f"/quotes/{created['id']}/action", json={"user_id": erin, "action": "APPROVE"}
        )
        assert denied.status_code == 403

        # Admin: matching catch-all role, SALES capability, not the creator
        resolved = client.post(
            f"/quotes/{created['id']}/action",
            json={"user_id": _uid(seeded_db, "alice.admin@example.com"), "action": "APPROVE"},
        )
        assert resolved.status_code == 200

    def test_capability_required_to_resolve_escalation(self, client, seeded_db):
        # Carol creates a 500 order (SUPERVISOR band); both capable
        # supervisors are inactive -> no eligible approver -> ESCALATED.
        _deactivate(seeded_db, "bob.supervisor@example.com", "hema.dual@example.com")
        carol = _uid(seeded_db, "carol.depthead@example.com")
        created = client.post(
            "/orders", json={"created_by": carol, "order_value": "500.00"}
        ).json()
        assert created["status"] == "ESCALATED"

        # Judy holds the matching SUPERVISOR role but has no PURCHASE capability
        denied = client.post(
            f"/orders/{created['id']}/action",
            json={"user_id": _uid(seeded_db, "judy.nocap@example.com"), "action": "APPROVE"},
        )
        assert denied.status_code == 403

        resolved = client.post(
            f"/orders/{created['id']}/action",
            json={"user_id": _uid(seeded_db, "alice.admin@example.com"), "action": "APPROVE"},
        )
        assert resolved.status_code == 200


class TestInactiveApproverFallback:
    """A deactivated assigned approver must not deadlock the entity."""

    def test_authority_holder_decides_when_approver_inactive(self, client, seeded_db):
        bob = _uid(seeded_db, "bob.supervisor@example.com")
        alice = _uid(seeded_db, "alice.admin@example.com")
        created = client.post(
            "/orders", json={"created_by": bob, "order_value": "75000.00"}
        ).json()
        assert created["status"] == "PENDING_APPROVAL"

        # While the assigned approver is active, nobody else may act
        denied = client.post(
            f"/orders/{created['id']}/action", json={"user_id": alice, "action": "APPROVE"}
        )
        assert denied.status_code == 403

        _deactivate(seeded_db, "carol.depthead@example.com")
        # Dave (Finance Head) holds no rule matching 75k -> still denied
        denied = client.post(
            f"/orders/{created['id']}/action",
            json={"user_id": _uid(seeded_db, "dave.financehead@example.com"),
                  "action": "APPROVE"},
        )
        assert denied.status_code == 403
        # Admin matches via the catch-all -> may decide
        resolved = client.post(
            f"/orders/{created['id']}/action", json={"user_id": alice, "action": "APPROVE"}
        )
        assert resolved.status_code == 200


class TestAgentLoopAndMisc:
    def test_content_only_llm_turn_is_tolerated(self, seeded_db):
        """Real models emit text-only turns; the loop must nudge, not fail."""

        class ChattyMock(MockChatModel):
            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                if not any(isinstance(m, AIMessage) for m in messages):
                    return ChatResult(
                        generations=[ChatGeneration(message=AIMessage(content="Let me check."))]
                    )
                return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

        bob = seeded_db.query(User).filter_by(email="bob.supervisor@example.com").one()
        order = Order(created_by=bob.id, order_value=Decimal("5000.00"))
        seeded_db.add(order)
        seeded_db.commit()
        result = process_entity(seeded_db, EntityType.ORDER, order.id, ChattyMock())
        assert result.final_status == EntityStatus.APPROVED

    def test_unknown_action_raises_typed_error(self, seeded_db):
        with pytest.raises(InvalidActionError):
            apply_action(seeded_db, EntityType.ORDER, 1, 1, "FOO")

    def test_audit_timestamp_is_timezone_aware(self, seeded_db, client):
        bob = _uid(seeded_db, "bob.supervisor@example.com")
        created = client.post(
            "/orders", json={"created_by": bob, "order_value": "100.00"}
        ).json()
        log = (
            seeded_db.query(ApprovalLog)
            .filter_by(entity_type=EntityType.ORDER, entity_id=created["id"])
            .first()
        )
        assert log.timestamp.tzinfo is not None
