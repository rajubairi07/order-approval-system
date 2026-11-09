"""Idempotent seed data: roles, capabilities, users, policies and rules.

Boundary semantics (spec decision D1): intervals are both-ends-inclusive at
2-decimal granularity, following the assignment's sample JSON. The assignment's
prose table says Sales Executive handles values *below* 25,000 while its sample
JSON gives them up to 25,000 inclusive; we follow the JSON (it is explicitly
labelled "Sample Database Data") and document the conflict in the README.

Admin authority (spec decision D2): Admin is seeded as a low-precedence
catch-all rule [0, NULL] with priority 100. Band rules carry priority 10.
The evaluator picks the highest-precedence (lowest number) matching rule as
the *required approver*, while a creator holding the role of *any* matching
rule is authorized to auto-approve — so Admin approves anything without any
hardcoded role check in Python.

Run:  python -m app.database.seed
"""

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    ApprovalPolicy,
    ApprovalRule,
    Capability,
    CapabilityType,
    EntityType,
    Role,
    User,
)

ROLE_NAMES = [
    "ADMIN",
    "SUPERVISOR",
    "DEPARTMENT_HEAD",
    "FINANCE_HEAD",
    "SALES_EXECUTIVE",
    "SALES_MANAGER",
    "BUSINESS_HEAD",
]

# (name, email, is_active, roles, capabilities)
USERS = [
    ("Alice Admin", "alice.admin@example.com", True, ["ADMIN"], ["PURCHASE", "SALES"]),
    ("Bob Supervisor", "bob.supervisor@example.com", True, ["SUPERVISOR"], ["PURCHASE"]),
    ("Carol DeptHead", "carol.depthead@example.com", True, ["DEPARTMENT_HEAD"], ["PURCHASE"]),
    ("Dave FinanceHead", "dave.financehead@example.com", True, ["FINANCE_HEAD"], ["PURCHASE"]),
    ("Erin SalesExec", "erin.salesexec@example.com", True, ["SALES_EXECUTIVE"], ["SALES"]),
    ("Frank SalesMgr", "frank.salesmgr@example.com", True, ["SALES_MANAGER"], ["SALES"]),
    ("Grace BizHead", "grace.bizhead@example.com", True, ["BUSINESS_HEAD"], ["SALES"]),
    # Dual-capability user (assignment test case 6)
    ("Hema Dual", "hema.dual@example.com", True, ["SUPERVISOR", "SALES_EXECUTIVE"], ["PURCHASE", "SALES"]),
    # Inactive approver — must never be selected as eligible
    ("Ivan Inactive", "ivan.inactive@example.com", False, ["DEPARTMENT_HEAD"], ["PURCHASE"]),
    # Has a role but NO capability (assignment test case 11)
    ("Judy NoCap", "judy.nocap@example.com", True, ["SUPERVISOR"], []),
]

BAND_PRIORITY = 10
CATCHALL_PRIORITY = 100

# (min, max, role, priority) — max None = unbounded
PURCHASE_RULES = [
    (Decimal("0.00"), Decimal("9999.99"), "SUPERVISOR", BAND_PRIORITY),
    (Decimal("10000.00"), Decimal("100000.00"), "DEPARTMENT_HEAD", BAND_PRIORITY),
    (Decimal("100000.01"), None, "FINANCE_HEAD", BAND_PRIORITY),
    (Decimal("0.00"), None, "ADMIN", CATCHALL_PRIORITY),
]

SALES_RULES = [
    (Decimal("0.00"), Decimal("25000.00"), "SALES_EXECUTIVE", BAND_PRIORITY),
    (Decimal("25000.01"), Decimal("200000.00"), "SALES_MANAGER", BAND_PRIORITY),
    (Decimal("200000.01"), None, "BUSINESS_HEAD", BAND_PRIORITY),
    (Decimal("0.00"), None, "ADMIN", CATCHALL_PRIORITY),
]


def seed(db: Session) -> None:
    """Insert seed data; safe to run repeatedly (keyed on unique columns)."""
    roles: dict[str, Role] = {}
    for name in ROLE_NAMES:
        role = db.query(Role).filter_by(name=name).one_or_none()
        if role is None:
            role = Role(name=name)
            db.add(role)
        roles[name] = role

    caps: dict[str, Capability] = {}
    for cap_type in CapabilityType:
        cap = db.query(Capability).filter_by(name=cap_type).one_or_none()
        if cap is None:
            cap = Capability(name=cap_type)
            db.add(cap)
        caps[cap_type.value] = cap

    db.flush()

    for name, email, is_active, role_names, cap_names in USERS:
        user = db.query(User).filter_by(email=email).one_or_none()
        if user is None:
            user = User(name=name, email=email, is_active=is_active)
            user.roles = [roles[r] for r in role_names]
            user.capabilities = [caps[c] for c in cap_names]
            db.add(user)

    _seed_policy(db, EntityType.ORDER, CapabilityType.PURCHASE, PURCHASE_RULES, roles)
    _seed_policy(db, EntityType.QUOTE, CapabilityType.SALES, SALES_RULES, roles)

    db.commit()


def _seed_policy(db, entity_type, capability, rule_rows, roles) -> None:
    policy = (
        db.query(ApprovalPolicy)
        .filter_by(entity_type=entity_type, capability=capability)
        .one_or_none()
    )
    if policy is not None:
        return
    policy = ApprovalPolicy(entity_type=entity_type, capability=capability, is_active=True)
    policy.rules = [
        ApprovalRule(
            min_value=min_v,
            max_value=max_v,
            allowed_role=roles[role_name],
            priority=priority,
        )
        for min_v, max_v, role_name, priority in rule_rows
    ]
    db.add(policy)


def main() -> None:
    from app.database.connection import SessionLocal, engine
    from app.models import Base

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    print("Database schema created and seed data inserted.")


if __name__ == "__main__":
    main()
