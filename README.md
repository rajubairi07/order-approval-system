# Agentic Order & Quote Approval Management System

A multi-agent order/quote approval backend built with **FastAPI + LangGraph + PostgreSQL + SQLAlchemy**, where LLM agents coordinate the workflow through database tools while deterministic Python enforces every business rule.

> Status: Phase 1 complete (schema, constraints, seed data, tests). Agents, graph, and APIs land in later phases — see [Build phases](#build-phases).

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows (source .venv/bin/activate on Unix)
pip install -r requirements.txt
cp .env.example .env              # defaults to mock LLM + local PostgreSQL
python -m app.database.seed       # create schema + seed data
pytest                            # test suite runs on in-memory SQLite, no services needed
```

## Design decisions

These resolve every ambiguity in the assignment; each is tested.

### D1 — Money and interval boundaries
All monetary values are `Numeric(12,2)`. Rule intervals are **both-ends-inclusive at 2-decimal granularity** (`[min_value, max_value]`, `max_value NULL` = unbounded). Adjacent bands differ by exactly `0.01`, so no 2-decimal value can fall in a gap.

**Documented inconsistency in the assignment:** the prose table says Sales Executive handles quotes *below* ₹25,000 (so ₹25,000 would belong to Sales Manager), but the sample JSON gives Sales Executive `max: 25000` inclusive. We follow the **sample JSON** — it is explicitly labelled "Sample Database Data" — so ₹25,000.00 → Sales Executive and ₹25,000.01 → Sales Manager.

### D2 — Admin authority as data, not code
Admin's unlimited authority is a seeded **catch-all rule** `[0, NULL]` with low precedence (`priority 100`; band rules are `priority 10`). The evaluator uses the rule set two ways:

- **Required approver** = role of the highest-precedence (lowest priority number) matching rule — so a ₹75,000 order requires a Department Head, never Admin.
- **Creator authority** = the creator holds the role of **any** matching rule (while active, with the required capability) — so an Admin auto-approves anything via the catch-all, and a Department Head auto-approves their own ₹75,000 order.

No role name appears in Python. Changing who has unlimited authority is a data change.

### D3 — Conflicts escalate
- Two *distinct* rules matching the same value at the same top priority → rule-priority conflict → `ESCALATED`.
- More than one active policy for the same (entity_type, capability) → policy conflict → `ESCALATED`. (Deliberately no unique constraint on active policies, so this path is reachable and tested.)
- No active policy / no matching rule → `ESCALATED`.

### D4 — Missing capability is checked by the agent, inside the graph
The entity is created (`CREATED`) and the graph runs; the **Context & Capability Agent** performs the check, as the assignment assigns it. Failure → `REJECTED` with remarks marking it a *workflow validation failure*, not a business rejection, plus an audit entry. (A pre-creation HTTP 403 would bypass the very agent being evaluated.)

### D5 — Role bands are strict, not hierarchical
A Finance Head is **not** the approver for a ₹5,000 order; only the required band role approves, and only Admin transcends bands (via the D2 catch-all). A role-hierarchy table was considered and deliberately left out as a schema extension beyond the assignment's rules.

### D6 — Per-step, structured + explained audit
One `ApprovalLog` row per agent action, carrying structured fields (`agent_name`, `action`, `policy_id`, `rule_applied`, `tool_called`, `performed_by`) **and** a concise natural-language `reasoning_trace`. Never raw LLM chain-of-thought. `policy_id` and `tool_called` extend the assignment's schema because its audit requirement demands the exact policy and tool trail, which `rule_applied` alone cannot convey.

### D7 — Approval action security (no auth system, documented simplification)
`POST /{entity}/{id}/action` takes `{user_id, action, remarks}`. Server-side: the user must be the assigned `current_approver` (or satisfy D8 for escalated entities), be active, and the transition must be legal. The entity row is locked with `SELECT … FOR UPDATE` in a transaction, so concurrent duplicate approvals cannot both succeed. Creators are excluded from `find_eligible_approvers`; among multiple eligible approvers the lowest user id wins (deterministic for tests).

### D8 — Escalation resolution
`ESCALATED → APPROVED/REJECTED` is allowed for any active user holding the role of **any** rule matching the value — in seed data that is Admin, again without a hardcoded role check.

### D9 — Mock LLM runs the real graph
One underlying chat model powers all five agents (different prompts + tool bindings). Mock mode substitutes a scripted deterministic chat model that emits the same tool calls and structured outputs, so mock and real modes exercise **identical** nodes, tools, Pydantic validation, and edges. A deterministic validator cross-checks every LLM-produced ID (policy, rule, user, role) against the database before it is acted on.

## State machine

```
CREATED ─→ APPROVED             creator satisfies any matching rule (D2)
CREATED ─→ PENDING_APPROVAL     approver assigned
CREATED ─→ REJECTED             capability validation failure (D4)
CREATED ─→ ESCALATED            no approver / no policy / conflict (D3)
PENDING_APPROVAL ─→ APPROVED | REJECTED    assigned approver via action API (D7)
ESCALATED        ─→ APPROVED | REJECTED    D8 predicate holder via action API
APPROVED / REJECTED are terminal; anything else → HTTP 409
```

## Seed data

7 roles, 2 capabilities, 2 active policies (4 rules each, incl. the Admin catch-all), and 10 users covering every edge case: one user per role, a dual-capability user (SUPERVISOR + SALES_EXECUTIVE), an inactive Department Head, and a Supervisor with no capabilities.

| Purchase (ORDER) band | Role | | Sales (QUOTE) band | Role |
|---|---|---|---|---|
| 0.00 – 9,999.99 | SUPERVISOR | | 0.00 – 25,000.00 | SALES_EXECUTIVE |
| 10,000.00 – 100,000.00 | DEPARTMENT_HEAD | | 25,000.01 – 200,000.00 | SALES_MANAGER |
| 100,000.01 – ∞ | FINANCE_HEAD | | 200,000.01 – ∞ | BUSINESS_HEAD |
| 0.00 – ∞ (priority 100) | ADMIN | | 0.00 – ∞ (priority 100) | ADMIN |

## Build phases

1. ✅ Schema, constraints, seed data, smoke tests
2. Database tools + deterministic policy evaluator
3. LangGraph state, agent nodes, mock chat model
4. Real LLM providers via `init_chat_model`
5. FastAPI endpoints + action API with locking
6. Per-step audit + audit endpoint
7. Full test matrix, architecture diagrams, Swagger polish

Schema migrations use `Base.metadata.create_all` + an idempotent seed script rather than Alembic — a deliberate scope trade-off for the 8–12h assignment; the models are Alembic-ready.
