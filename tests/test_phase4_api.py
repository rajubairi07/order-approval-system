"""Phase 4: FastAPI endpoints end-to-end (mock LLM, in-memory SQLite)."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database.connection import get_db
from app.main import app
from app.models import User


@pytest.fixture()
def client(engine, seeded_db):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_db, None)


def _uid(db, email) -> int:
    return db.query(User).filter_by(email=email).one().id


class TestOrderLifecycle:
    def test_admin_order_auto_approved(self, client, seeded_db):
        resp = client.post(
            "/orders",
            json={"created_by": _uid(seeded_db, "alice.admin@example.com"),
                  "order_value": "500000.00"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "APPROVED"
        assert body["current_approver"] is None
        assert "auto-approved" in body["status_reason"]

    def test_full_pending_then_human_approve(self, client, seeded_db):
        bob = _uid(seeded_db, "bob.supervisor@example.com")
        carol = _uid(seeded_db, "carol.depthead@example.com")

        created = client.post(
            "/orders", json={"created_by": bob, "order_value": "75000.00"}
        ).json()
        assert created["status"] == "PENDING_APPROVAL"
        assert created["current_approver"] == carol

        fetched = client.get(f"/orders/{created['id']}").json()
        assert Decimal(fetched["order_value"]) == Decimal("75000.00")

        acted = client.post(
            f"/orders/{created['id']}/action",
            json={"user_id": carol, "action": "APPROVE", "remarks": "Budget available"},
        )
        assert acted.status_code == 200
        assert acted.json()["status"] == "APPROVED"

        trail = client.get(f"/audit/ORDER/{created['id']}").json()
        assert trail[-1]["action"] == "human_approve"
        assert trail[-1]["performed_by"] == carol
        assert trail[-1]["remarks"] == "Budget available"
        # Human action is its own single-step execution: no NULL steps anywhere
        assert trail[-1]["step_number"] == 1
        assert trail[-1]["execution_id"] is not None
        assert trail[-1]["execution_id"] != trail[0]["execution_id"]
        assert all(t["step_number"] is not None for t in trail)

    def test_approver_rejects(self, client, seeded_db):
        bob = _uid(seeded_db, "bob.supervisor@example.com")
        carol = _uid(seeded_db, "carol.depthead@example.com")
        created = client.post(
            "/orders", json={"created_by": bob, "order_value": "75000.00"}
        ).json()
        acted = client.post(
            f"/orders/{created['id']}/action",
            json={"user_id": carol, "action": "REJECT", "remarks": "Over budget"},
        )
        assert acted.status_code == 200
        assert acted.json()["status"] == "REJECTED"

    def test_unauthorized_approval_attempt_403(self, client, seeded_db):
        bob = _uid(seeded_db, "bob.supervisor@example.com")
        created = client.post(
            "/orders", json={"created_by": bob, "order_value": "75000.00"}
        ).json()
        # Dave (Finance Head) is not the assigned approver
        resp = client.post(
            f"/orders/{created['id']}/action",
            json={"user_id": _uid(seeded_db, "dave.financehead@example.com"),
                  "action": "APPROVE"},
        )
        assert resp.status_code == 403

    def test_duplicate_action_409(self, client, seeded_db):
        bob = _uid(seeded_db, "bob.supervisor@example.com")
        carol = _uid(seeded_db, "carol.depthead@example.com")
        created = client.post(
            "/orders", json={"created_by": bob, "order_value": "75000.00"}
        ).json()
        first = client.post(
            f"/orders/{created['id']}/action",
            json={"user_id": carol, "action": "APPROVE"},
        )
        assert first.status_code == 200
        second = client.post(
            f"/orders/{created['id']}/action",
            json={"user_id": carol, "action": "REJECT"},
        )
        assert second.status_code == 409  # invalid transition: APPROVED is terminal

    def test_escalated_resolved_by_admin_only(self, client, seeded_db):
        carol = seeded_db.query(User).filter_by(email="carol.depthead@example.com").one()
        carol.is_active = False
        seeded_db.commit()
        bob = _uid(seeded_db, "bob.supervisor@example.com")

        created = client.post(
            "/orders", json={"created_by": bob, "order_value": "75000.00"}
        ).json()
        assert created["status"] == "ESCALATED"

        # Bob lacks matching-rule authority at this value -> 403 (D8)
        denied = client.post(
            f"/orders/{created['id']}/action",
            json={"user_id": bob, "action": "APPROVE"},
        )
        assert denied.status_code == 403

        # Admin matches via the catch-all rule -> allowed
        resolved = client.post(
            f"/orders/{created['id']}/action",
            json={"user_id": _uid(seeded_db, "alice.admin@example.com"),
                  "action": "APPROVE"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "APPROVED"


class TestQuotesAndValidation:
    def test_quote_flow_reuses_engine(self, client, seeded_db):
        erin = _uid(seeded_db, "erin.salesexec@example.com")
        frank = _uid(seeded_db, "frank.salesmgr@example.com")
        created = client.post(
            "/quotes", json={"created_by": erin, "quote_value": "100000.00"}
        ).json()
        assert created["status"] == "PENDING_APPROVAL"
        assert created["current_approver"] == frank

    def test_rejected_for_missing_capability(self, client, seeded_db):
        judy = _uid(seeded_db, "judy.nocap@example.com")
        created = client.post(
            "/orders", json={"created_by": judy, "order_value": "100.00"}
        ).json()
        assert created["status"] == "REJECTED"
        assert "validation failure" in created["status_reason"]

    def test_unknown_entity_404(self, client):
        assert client.get("/orders/99999").status_code == 404

    def test_unknown_creator_404(self, client):
        resp = client.post("/orders", json={"created_by": 99999, "order_value": "10.00"})
        assert resp.status_code == 404

    @pytest.mark.parametrize("bad_value", ["-5.00", "0", "10.123"])
    def test_invalid_values_422(self, client, seeded_db, bad_value):
        bob = _uid(seeded_db, "bob.supervisor@example.com")
        resp = client.post("/orders", json={"created_by": bob, "order_value": bad_value})
        assert resp.status_code == 422


class TestPolicyAndAuditApis:
    def test_list_policies(self, client):
        policies = client.get("/policies").json()
        assert len(policies) == 2
        assert all(len(p["rules"]) == 4 for p in policies)

    def test_create_policy_and_conflict_behaviour(self, client, seeded_db):
        admin_role_id = client.get("/policies").json()[0]["rules"][-1]["allowed_role_id"]
        resp = client.post(
            "/policies",
            json={
                "entity_type": "ORDER",
                "capability": "PURCHASE",
                "is_active": True,
                "rules": [{"min_value": "0.00", "max_value": None,
                           "allowed_role_id": admin_role_id, "priority": 10}],
            },
        )
        assert resp.status_code == 201

        # Two active ORDER policies now exist -> workflow escalates (D3)
        bob = _uid(seeded_db, "bob.supervisor@example.com")
        created = client.post(
            "/orders", json={"created_by": bob, "order_value": "100.00"}
        ).json()
        assert created["status"] == "ESCALATED"

    def test_create_policy_unknown_role_422(self, client):
        resp = client.post(
            "/policies",
            json={
                "entity_type": "QUOTE",
                "capability": "SALES",
                "is_active": False,
                "rules": [{"min_value": "0.00", "max_value": "10.00",
                           "allowed_role_id": 99999, "priority": 10}],
            },
        )
        assert resp.status_code == 422

    def test_audit_trail_contents(self, client, seeded_db):
        bob = _uid(seeded_db, "bob.supervisor@example.com")
        created = client.post(
            "/orders", json={"created_by": bob, "order_value": "75000.00"}
        ).json()
        trail = client.get(f"/audit/ORDER/{created['id']}").json()
        actions = [t["action"] for t in trail]
        assert actions == [
            "validate_capability",
            "evaluate_policy",
            "authority_check",
            "route_for_approval",
            "final_decision",
        ]
        policy_step = next(t for t in trail if t["action"] == "evaluate_policy")
        assert policy_step["policy_id"] is not None
        assert policy_step["rule_applied"] is not None
        assert "DEPARTMENT_HEAD" in policy_step["reasoning_trace"]
        execution_ids = {t["execution_id"] for t in trail}
        assert len(execution_ids) == 1 and None not in execution_ids
        assert [t["step_number"] for t in trail] == [1, 2, 3, 4, 5]
