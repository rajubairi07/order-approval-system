"""Deterministic policy evaluation (spec decisions D1-D3).

Pure functions over validated schema objects: no LLM, no database session.
Agents call these through tools; the LLM coordinates and explains, it never
decides thresholds (assignment's core architectural principle).
"""

from decimal import Decimal

from app.schemas.approval import (
    DecisionOutcome,
    PolicyDecision,
    PolicyInfo,
    RuleInfo,
)


def rule_matches(rule: RuleInfo, value: Decimal) -> bool:
    """Both-ends-inclusive interval check (D1)."""
    return rule.min_value <= value and (rule.max_value is None or value <= rule.max_value)


def evaluate_policy(
    policies: list[PolicyInfo],
    rules_by_policy: dict[int, list[RuleInfo]],
    value: Decimal,
) -> PolicyDecision:
    """Resolve the required approver rule for a transaction value.

    Precedence: lower priority number wins (D2). Ambiguity at the top
    (distinct rules tied on priority) or at policy level (multiple active
    policies) is a conflict, never an arbitrary pick (D3).
    """
    active = [p for p in policies if p.is_active]

    if not active:
        return PolicyDecision(
            outcome=DecisionOutcome.NO_ACTIVE_POLICY,
            explanation="No active approval policy exists for this entity type and capability.",
        )

    if len(active) > 1:
        ids = sorted(p.id for p in active)
        return PolicyDecision(
            outcome=DecisionOutcome.POLICY_CONFLICT,
            explanation=f"Policy conflict: multiple active policies {ids} apply; escalating for resolution.",
        )

    policy = active[0]
    matching = [r for r in rules_by_policy.get(policy.id, []) if rule_matches(r, value)]

    if not matching:
        return PolicyDecision(
            outcome=DecisionOutcome.NO_MATCHING_RULE,
            policy_id=policy.id,
            explanation=(
                f"No rule in policy {policy.id} matches the value {value}; escalating."
            ),
        )

    top_priority = min(r.priority for r in matching)
    top = [r for r in matching if r.priority == top_priority]

    if len(top) > 1:
        ids = sorted(r.id for r in top)
        return PolicyDecision(
            outcome=DecisionOutcome.RULE_PRIORITY_CONFLICT,
            policy_id=policy.id,
            matching_rule_ids=sorted(r.id for r in matching),
            explanation=(
                f"Rule priority conflict: rules {ids} in policy {policy.id} both match "
                f"value {value} at priority {top_priority}; escalating."
            ),
        )

    required = top[0]
    bound = "unlimited" if required.max_value is None else str(required.max_value)
    return PolicyDecision(
        outcome=DecisionOutcome.RULE_SELECTED,
        policy_id=policy.id,
        required_rule_id=required.id,
        required_role_id=required.allowed_role_id,
        required_role_name=required.allowed_role_name,
        matching_rule_ids=sorted(r.id for r in matching),
        matching_role_ids=sorted({r.allowed_role_id for r in matching}),
        explanation=(
            f"Value {value} falls within the {required.allowed_role_name} band "
            f"[{required.min_value} - {bound}] under rule {required.id} of policy {policy.id}."
        ),
    )


def creator_has_authority(decision: PolicyDecision, creator_role_ids: set[int]) -> bool:
    """Creator auto-approves iff they hold the role of ANY matching rule (D2).

    Admin's catch-all rule matches every value, so an Admin creator is always
    authorized — with no role name hardcoded here.
    """
    if decision.outcome != DecisionOutcome.RULE_SELECTED:
        return False
    return bool(set(decision.matching_role_ids) & creator_role_ids)
