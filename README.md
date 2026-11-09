# Agentic Order & Quote Approval Management System

A multi-agent approval backend for **purchase orders** and **sales quotes**, built with **FastAPI + LangGraph + PostgreSQL + SQLAlchemy**. Five specialized agents coordinate through a shared LangGraph `StateGraph`; every business rule lives in the database and reaches agents only through typed tools; every decision is structured, validated, deterministic where it must be, and fully audited with natural-language explanations.

## Quick start (no API keys, no services)

```bash
python -m venv .venv
.venv/Scripts/activate              # Windows (source .venv/bin/activate on Unix)
pip install -r requirements.txt
pytest                              # 97 tests on in-memory SQLite, mock LLM
```

## Running the API

```bash
docker compose up -d                # local PostgreSQL 16
cp .env.example .env                # defaults: mock LLM + local PostgreSQL
python -m app.database.seed         # create schema + seed data
uvicorn app.main:app --reload       # Swagger at http://localhost:8000/docs
```

To use a real LLM, set in `.env`: `LLM_PROVIDER=openai|anthropic|google_genai|ollama`, optionally `LLM_MODEL`, and the provider's standard API-key variable. **Mock mode runs the identical workflow** — same graph, same tools, same validation (see D9).

```bash
# Try it: Bob (Supervisor) creates a 75k order -> routed to a Department Head
curl -X POST localhost:8000/orders -H "Content-Type: application/json" \
     -d '{"created_by": 2, "order_value": "75000.00"}'
# Carol (Department Head, user 3) approves
curl -X POST localhost:8000/orders/1/action -H "Content-Type: application/json" \
     -d '{"user_id": 3, "action": "APPROVE", "remarks": "Budget available"}'
# Step-by-step agent reasoning
curl localhost:8000/audit/ORDER/1
```

## Architecture

### The five agents

| Agent | Kind | Tools | Responsibility |
|---|---|---|---|
| Supervisor / Orchestrator | deterministic (the graph itself) | — | Topology, conditional handoffs, authority check, failure fallback → ESCALATED |
| Context & Capability | **LLM, tool-calling** | `get_entity_details`, `get_user_details`, `get_user_roles`, `get_user_capabilities` | Identify creator; validate required capability |
| Policy Evaluation | **LLM, tool-calling** | `fetch_active_policies`, `get_approval_rules` | Find the governing DB rule for the value; report conflicts |
| Routing & Workflow | **LLM, tool-calling** | `find_eligible_approvers` | Pick an active, capable, non-creator approver |
| Audit & Explainability | deterministic | `assign_approver`, `update_entity_status`, `persist_audit_log` | Apply state, persist per-step trail + final summary |

Each LLM agent runs a shared tool-calling loop ([app/agents/base.py](app/agents/base.py)): the model requests data tools, results stream back as tool messages, and the agent finishes by calling its `submit_*` tool whose arguments are a Pydantic schema — the structured handoff. A **deterministic guardrail** then recomputes the ground truth from the database and overrides any divergent LLM submission (recorded in the audit trail), so a hallucinated role, user, policy or rule id can never act.

Division of labour (the assignment's core principle): **LLMs coordinate and explain; deterministic Python enforces.** Policy matching, authority, state transitions, approver authorization and audit writes are all deterministic code paths — the LLM cannot alter them, only narrate and traverse them.

### Graph topology (shared by ORDER and QUOTE)

```
START
  └─ load_entity ──────────────── (error) ─────────────┐
       └─ context (Context & Capability Agent)          │
            ├─ capability missing → REJECTED ───────────┤
            └─ policy (Policy Evaluation Agent)         │
                 ├─ no policy / conflict → ESCALATED ───┤
                 └─ authority (deterministic, D2)       │
                      ├─ creator authorized → APPROVED ─┤
                      └─ routing (Routing Agent)        │
                           ├─ approver → PENDING_APPROVAL
                           └─ none → ESCALATED ─────────┤
                                                        ▼
                                  audit (persist trail, apply status) → END
```

One compiled graph serves both entity types; the request's DB session and chat model travel in LangGraph's `config["configurable"]`. Every node is wrapped so an unexpected failure escalates with an audit entry instead of crashing mid-workflow.

### State machine

```
CREATED → APPROVED | PENDING_APPROVAL | REJECTED | ESCALATED
PENDING_APPROVAL → APPROVED | REJECTED     (assigned approver, via action API)
ESCALATED        → APPROVED | REJECTED     (matching-rule authority holder, D8)
APPROVED / REJECTED are terminal; anything else → HTTP 409
```

### Project structure

```
app/
├── main.py, config.py          FastAPI app, env settings
├── api/                        orders, quotes, policies, audit routers
├── agents/                     base loop, toolkits, 4 agents, orchestrator
├── graph/                      state (Pydantic), nodes, workflow (StateGraph)
├── llm/                        provider selection, deterministic MockChatModel
├── models/                     10 SQLAlchemy entities
├── schemas/                    request/response + structured agent outputs
├── services/                   evaluator, state machine, entity/action service
├── tools/                      10 typed database tools
└── database/                   engine/session, idempotent seed
tests/                          97 tests across 5 phases
```

## Design decisions

### D1 — Money and interval boundaries
All monetary values are `Numeric(12,2)` (API validates ≤ 2 decimals). Rule intervals are **both-ends-inclusive at 2-decimal granularity**; adjacent bands differ by exactly `0.01`, so no representable value falls in a gap.

**Documented assignment inconsistency:** the prose table says Sales Executive handles quotes *below* ₹25,000, but the sample JSON gives them `max: 25000` inclusive. We follow the **sample JSON** (explicitly labelled "Sample Database Data"): ₹25,000.00 → Sales Executive, ₹25,000.01 → Sales Manager. Both boundaries are tested.

### D2 — Admin authority as data, not code
Admin's unlimited authority is a seeded **catch-all rule** `[0, NULL]` at `priority 100` (band rules are `priority 10`). The rule set is used two ways: the **required approver** is the role of the highest-precedence matching rule (so ₹75,000 requires a Department Head, never Admin), while a **creator auto-approves** when they hold the role of *any* matching rule (so Admin approves everything via the catch-all, and a Department Head auto-approves their own in-band order). No role name appears in Python; changing who has unlimited authority is a data change.

*Alternative considered:* modelling Admin as an `APPROVE_ANY` permission via role–permission tables. Rejected because it adds tables beyond the assignment's schema and splits authority across two mechanisms, where the catch-all rule keeps creator authority, routing precedence and escalation resolution behind one predicate — and tests prove the catch-all never hijacks routing.

### D3 — Ambiguity escalates
Multiple active policies for one (entity_type, capability) → policy conflict; distinct rules tied at the top priority → rule-priority conflict; no active policy or no matching rule — all → `ESCALATED`, never an arbitrary pick. There is deliberately no unique constraint on active policies so the conflict path is reachable and tested.

### D4 — Missing capability is checked by the agent, inside the graph
The entity is created and the Context & Capability Agent performs the check (as the assignment assigns it); failure → `REJECTED` with remarks marking it a *workflow validation failure*, not a business rejection. A pre-creation HTTP 403 would bypass the very agent being evaluated.

### D5 — Role bands are strict, not hierarchical
A Finance Head is **not** the approver for a ₹5,000 order; only Admin transcends bands (via D2). A role-hierarchy table was considered and deliberately left out as beyond the assignment's rules.

### D6 — Per-step, structured + explained audit
One `ApprovalLog` row per agent action with structured fields (`agent_name`, `action`, `policy_id`, `rule_applied`, `tool_called`, `performed_by`, `execution_id`, `step_number`) **and** a concise natural-language `reasoning_trace` — never raw LLM chain-of-thought. `policy_id`, `tool_called`, `execution_id` and `step_number` extend the assignment's schema because its audit requirement demands the exact policy, the tool trail, and an ordered step-by-step trace of each workflow run. A human APPROVE/REJECT is recorded as its own single-step execution (fresh `execution_id`, `step_number` 1), so a trail never mixes numbered and NULL steps.

### D7 — Approval action security (documented simplification: no auth system)
`POST /{entity}/{id}/action` takes `{user_id, action, remarks}`. Server-side: actor must be the assigned `current_approver` (or satisfy D8 for escalated entities), be active, and the transition must be legal. The row is locked with `SELECT … FOR UPDATE` (PostgreSQL) for the whole check-and-transition, so concurrent duplicate actions serialize and the loser gets 409. Creators are excluded from eligibility (no self-approval); among multiple eligible approvers the lowest user id wins (deterministic).

### D8 — Escalation resolution
`ESCALATED → APPROVED/REJECTED` is allowed for any active user holding the role of **any rule matching the value** — in seed data that's Admin via the catch-all, again with no hardcoded role.

### D9 — Mock LLM runs the real graph
`MockChatModel` subclasses LangChain's `BaseChatModel` and deterministically scripts each agent's tool calls through the **identical** loop, tools, schemas and edges as a real model. Guardrails cross-check every LLM-produced id against the database in both modes.

## Database

Entities exactly as assigned: `User`, `Role`, `UserRole`, `Capability`, `UserCapability`, `ApprovalPolicy`, `ApprovalRule`, `Order`, `Quote`, `ApprovalLog` — with FKs, unique constraints (`email`, role/capability names), check constraints (`min_value >= 0`, `max >= min`, `value > 0`) and lookup indexes.

Seed data: 7 roles, 2 capabilities, 2 active policies (4 rules each incl. the Admin catch-all), and 10 users covering every edge case — one per role, a dual-capability user, an inactive Department Head, and a Supervisor with no capabilities.

| Purchase (ORDER) | Role | | Sales (QUOTE) | Role |
|---|---|---|---|---|
| 0.00 – 9,999.99 | SUPERVISOR | | 0.00 – 25,000.00 | SALES_EXECUTIVE |
| 10,000.00 – 100,000.00 | DEPARTMENT_HEAD | | 25,000.01 – 200,000.00 | SALES_MANAGER |
| 100,000.01 – ∞ | FINANCE_HEAD | | 200,000.01 – ∞ | BUSINESS_HEAD |
| 0.00 – ∞ (prio 100) | ADMIN | | 0.00 – ∞ (prio 100) | ADMIN |

## APIs

| Method & path | Purpose |
|---|---|
| `POST /orders`, `POST /quotes` | Create + run the multi-agent workflow (201, returns outcome + reason) |
| `GET /orders/{id}`, `GET /quotes/{id}` | Read entity state |
| `POST /orders/{id}/action`, `POST /quotes/{id}/action` | Human APPROVE/REJECT (403 unauthorized, 409 illegal transition) |
| `GET /policies`, `POST /policies` | List / create policies with nested rules |
| `GET /audit/{entity_type}/{id}` | Step-by-step agent decisions and reasoning traces |
| `GET /health`, `GET /docs` | Health check, Swagger UI |

## Test matrix (assignment cases → tests)

All 16 required cases are covered across `tests/` (97 tests): admin auto-approval (1), supervisor low/high value (2, 3), sales-exec low/high quote (4, 5), dual-capability user (6), no eligible approver (7), inactive policy (8), overlapping rules (9), priority conflicts (10), missing capability (11), unauthorized approval → 403 (12), invalid transitions → 409 (13), approver rejection (14), audit policy/rule ids (15), and mock mode with zero credentials (16 — the entire suite runs this way, plus explicit provider tests). Boundary values (9,999.99 / 10,000.00 / 25,000.00 / 25,000.01 / 100,000.00 / 100,000.01) are tested exactly.

## Scope notes

- **Migrations:** `Base.metadata.create_all` + idempotent seed instead of Alembic — a deliberate trade-off for the 8–12h scope; the models are Alembic-ready. Note `create_all` never ALTERs existing tables: after pulling a schema change, recreate the dev database (`docker compose down -v && docker compose up -d && python -m app.database.seed`).
- **Tests on SQLite:** fast and service-free; `FOR UPDATE` is a no-op on SQLite and takes effect on PostgreSQL.
- **HITL interrupt/resume:** the good-to-have LangGraph `interrupt()` pattern is deferred; the API-based human approval flow (assignment's required path) is complete.
- **Auth:** actor identity travels in the payload by design (D7); a real deployment would replace it with authenticated identity.
- **Session lifetime:** one session spans a workflow run so all writes (concentrated in the final audit node) commit atomically. With a real LLM this keeps a connection open for the run's duration — acceptable here; a production deployment would move graph execution to a worker queue and shorten read transactions.
