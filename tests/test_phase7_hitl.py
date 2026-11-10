"""Phase 7: human-in-the-loop interrupt/resume (spec D10).

The graph pauses at PENDING_APPROVAL via LangGraph's interrupt(), checkpoints
its state, and the approver's API action resumes that same run. These tests
pin the three properties that make the pattern meaningful:

  * the run really is suspended (not finished and restarted),
  * the decision continues the original execution's audit trail,
  * authorisation still happens in the service layer, before any resume.
"""

from decimal import Decimal

import pytest

from app.agents.orchestrator import (
    awaiting_human_decision,
    process_entity,
    resume_entity,
)
from app.graph.workflow import get_workflow, thread_id
from app.llm.mock import MockChatModel
from app.models import ApprovalLog, Order, User
from app.models.enums import EntityStatus, EntityType

from tests.test_phase4_api import client, _uid  # noqa: F401


def _logs(db, entity_id):
    return (
        db.query(ApprovalLog)
        .filter_by(entity_type=EntityType.ORDER, entity_id=entity_id)
        .order_by(ApprovalLog.id)
        .all()
    )


def _order(db, email, value):
    uid = db.query(User).filter_by(email=email).one().id
    o = Order(created_by=uid, order_value=Decimal(value))
    db.add(o)
    db.commit()
    return o


class TestInterrupt:
    def test_run_pauses_at_pending_approval(self, seeded_db):
        order = _order(seeded_db, "bob.supervisor@example.com", "75000.00")
        result = process_entity(seeded_db, EntityType.ORDER, order.id, MockChatModel())

        assert result.final_status == EntityStatus.PENDING_APPROVAL
        # The run is suspended, not finished
        assert awaiting_human_decision(EntityType.ORDER, order.id)
        snapshot = get_workflow().get_state(
            {"configurable": {"thread_id": thread_id(EntityType.ORDER, order.id)}}
        )
        assert "human_decision" in snapshot.next

        # ...and the status + trail are already durable while it waits
        seeded_db.refresh(order)
        assert order.status == EntityStatus.PENDING_APPROVAL
        assert len(_logs(seeded_db, order.id)) == 5

    def test_interrupt_payload_describes_what_is_needed(self, seeded_db):
        order = _order(seeded_db, "bob.supervisor@example.com", "75000.00")
        process_entity(seeded_db, EntityType.ORDER, order.id, MockChatModel())

        snapshot = get_workflow().get_state(
            {"configurable": {"thread_id": thread_id(EntityType.ORDER, order.id)}}
        )
        payload = snapshot.tasks[0].interrupts[0].value
        carol = seeded_db.query(User).filter_by(
            email="carol.depthead@example.com").one()
        assert payload["awaiting"] == "approval_decision"
        assert payload["entity_id"] == order.id
        assert payload["assigned_approver_id"] == carol.id
        assert payload["required_role"] == "DEPARTMENT_HEAD"
        assert payload["accepts"] == ["APPROVE", "REJECT"]

    def test_outcomes_needing_no_human_do_not_pause(self, seeded_db):
        """Auto-approval, rejection and escalation end the run outright."""
        approved = _order(seeded_db, "alice.admin@example.com", "500000.00")
        rejected = _order(seeded_db, "judy.nocap@example.com", "500.00")
        for order, expected in ((approved, EntityStatus.APPROVED),
                                (rejected, EntityStatus.REJECTED)):
            result = process_entity(
                seeded_db, EntityType.ORDER, order.id, MockChatModel())
            assert result.final_status == expected
            assert not awaiting_human_decision(EntityType.ORDER, order.id)


class TestResume:
    def test_approval_resumes_the_same_execution(self, client, seeded_db):
        bob = _uid(seeded_db, "bob.supervisor@example.com")
        carol = _uid(seeded_db, "carol.depthead@example.com")
        created = client.post(
            "/orders", json={"created_by": bob, "order_value": "75000.00"}
        ).json()
        assert created["status"] == "PENDING_APPROVAL"

        resp = client.post(
            f"/orders/{created['id']}/action",
            json={"user_id": carol, "action": "APPROVE", "remarks": "Within budget"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "APPROVED"

        trail = client.get(f"/audit/ORDER/{created['id']}").json()
        assert [t["step_number"] for t in trail] == [1, 2, 3, 4, 5, 6]
        assert len({t["execution_id"] for t in trail}) == 1   # one run, not two
        last = trail[-1]
        assert last["action"] == "human_approve"
        assert last["performed_by"] == carol
        assert last["remarks"] == "Within budget"
        assert "on resume of execution" in last["reasoning_trace"]
        # the resumed step still carries the policy context from the run
        assert last["policy_id"] is not None and last["rule_applied"] is not None

    def test_rejection_resumes_too(self, client, seeded_db):
        bob = _uid(seeded_db, "bob.supervisor@example.com")
        carol = _uid(seeded_db, "carol.depthead@example.com")
        created = client.post(
            "/orders", json={"created_by": bob, "order_value": "75000.00"}
        ).json()
        resp = client.post(
            f"/orders/{created['id']}/action",
            json={"user_id": carol, "action": "REJECT", "remarks": "Not needed"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "REJECTED"
        assert client.get(f"/audit/ORDER/{created['id']}").json()[-1]["action"] == "human_reject"

    def test_checkpoint_is_released_after_resume(self, client, seeded_db):
        bob = _uid(seeded_db, "bob.supervisor@example.com")
        carol = _uid(seeded_db, "carol.depthead@example.com")
        created = client.post(
            "/orders", json={"created_by": bob, "order_value": "75000.00"}
        ).json()
        assert awaiting_human_decision(EntityType.ORDER, created["id"])
        client.post(f"/orders/{created['id']}/action",
                    json={"user_id": carol, "action": "APPROVE"})
        assert not awaiting_human_decision(EntityType.ORDER, created["id"])


class TestAuthorizationPrecedesResume:
    """The graph applies the decision; it never decides who may make it."""

    def test_unauthorized_actor_cannot_resume(self, client, seeded_db):
        bob = _uid(seeded_db, "bob.supervisor@example.com")
        created = client.post(
            "/orders", json={"created_by": bob, "order_value": "75000.00"}
        ).json()

        resp = client.post(
            f"/orders/{created['id']}/action",
            json={"user_id": _uid(seeded_db, "dave.financehead@example.com"),
                  "action": "APPROVE"},
        )
        assert resp.status_code == 403
        # the run is still parked, untouched, and still approvable by Carol
        assert awaiting_human_decision(EntityType.ORDER, created["id"])
        assert client.get(f"/orders/{created['id']}").json()["status"] == "PENDING_APPROVAL"
        assert len(client.get(f"/audit/ORDER/{created['id']}").json()) == 5

        ok = client.post(
            f"/orders/{created['id']}/action",
            json={"user_id": _uid(seeded_db, "carol.depthead@example.com"),
                  "action": "APPROVE"},
        )
        assert ok.status_code == 200

    def test_creator_cannot_resume_their_own_order(self, client, seeded_db):
        bob = _uid(seeded_db, "bob.supervisor@example.com")
        created = client.post(
            "/orders", json={"created_by": bob, "order_value": "75000.00"}
        ).json()
        resp = client.post(
            f"/orders/{created['id']}/action",
            json={"user_id": bob, "action": "APPROVE"},
        )
        assert resp.status_code == 403


class TestFallbackWhenNotPaused:
    def test_hitl_disabled_uses_the_direct_path(self, seeded_db):
        """With HITL off the run ends at PENDING_APPROVAL and the approval is
        applied directly, as its own single-step execution."""
        from app.services.entity_service import apply_action

        order = _order(seeded_db, "bob.supervisor@example.com", "75000.00")
        result = process_entity(
            seeded_db, EntityType.ORDER, order.id, MockChatModel(), hitl=False)
        assert result.final_status == EntityStatus.PENDING_APPROVAL
        assert not awaiting_human_decision(EntityType.ORDER, order.id)

        carol = seeded_db.query(User).filter_by(
            email="carol.depthead@example.com").one()
        info = apply_action(
            seeded_db, EntityType.ORDER, order.id, carol.id, "APPROVE", "ok")
        assert info.status == EntityStatus.APPROVED

        logs = _logs(seeded_db, order.id)
        assert logs[-1].action == "human_approve"
        assert logs[-1].step_number == 1                       # own execution
        assert logs[-1].execution_id != logs[0].execution_id

    def test_lost_checkpoint_still_approvable(self, seeded_db):
        """Simulates a process restart: the paused run's checkpoint is gone,
        so the decision must still apply via the fallback path."""
        from app.graph.workflow import reset_checkpoints
        from app.services.entity_service import apply_action

        order = _order(seeded_db, "bob.supervisor@example.com", "75000.00")
        process_entity(seeded_db, EntityType.ORDER, order.id, MockChatModel())
        assert awaiting_human_decision(EntityType.ORDER, order.id)

        reset_checkpoints()                                    # "restart"
        assert not awaiting_human_decision(EntityType.ORDER, order.id)

        carol = seeded_db.query(User).filter_by(
            email="carol.depthead@example.com").one()
        info = apply_action(
            seeded_db, EntityType.ORDER, order.id, carol.id, "APPROVE")
        assert info.status == EntityStatus.APPROVED
