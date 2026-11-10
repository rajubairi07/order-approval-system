# Agentic Order & Quote Approval Management System

A multi-agent approval backend for **purchase orders** and **sales quotes**, built with **FastAPI + LangGraph + PostgreSQL + SQLAlchemy**. Five specialized agents coordinate through a shared LangGraph `StateGraph`; every business rule lives in the database and reaches agents only through typed tools; every decision is structured, validated, deterministic where it must be, and fully audited with natural-language explanations.

## Quick start — the whole system, no services, no API keys

```bash
python -m venv .venv
.venv/Scripts/activate              # Windows (source .venv/bin/activate on Unix)
pip install -r requirements.txt
pytest                              # 115 tests on in-memory SQLite, mock LLM
```

That runs every workflow end to end — the real graph, the real agents, the real tools — against a throwaway database with the deterministic mock model. Nothing to install, nothing to configure.

## Running the API

**Option A — SQLite, zero setup.** Good enough to click through Swagger and exercise every endpoint:

```bash
cp .env.example .env
# Unix:    export DATABASE_URL="sqlite:///./approval.db"
# Windows: set DATABASE_URL=sqlite:///./approval.db
python -m app.database.seed         # create schema + seed data
uvicorn app.main:app --reload       # Swagger at http://localhost:8000/docs
```

**Option B — PostgreSQL**, the intended production backend. Create an empty database, point `DATABASE_URL` at it in `.env`, then run the same two commands:

```bash
createdb order_approval             # or: CREATE DATABASE order_approval;
# .env: DATABASE_URL=postgresql+psycopg2://postgres:<password>@localhost:5432/order_approval
python -m app.database.seed
uvicorn app.main:app --reload
```

The models are backend-agnostic — `create_all` emits whatever DDL the connected engine speaks — so the same code serves both. Row locking (`SELECT … FOR UPDATE`, see D7) is a no-op on SQLite and takes effect on PostgreSQL.

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

### Tool definitions

Ten tools in [app/tools/](app/tools/) are the **only** code permitted to touch the database. Agents receive them as LangChain `StructuredTool`s whose `func` closes over the request's `Session` ([app/agents/toolkits.py](app/agents/toolkits.py)) — the session never enters the model's context, so "don't invent a policy" is enforced by what the agent can physically reach, not by instruction. Every tool takes `db: Session` as its first argument (bound, not exposed), returns a Pydantic schema rather than an ORM row, and **flushes but never commits**: the unit of work belongs to the caller, so a whole workflow run either fully persists or fully rolls back.

| Tool | Signature (after the bound `db`) | Returns | Raises / notes |
|---|---|---|---|
| `get_entity_details` | `(entity_type, entity_id, *, for_update=False)` | `EntityInfo` | `NotFoundError`. `for_update=True` emits `SELECT … FOR UPDATE` (D7). Projects both `orders` and `quotes` into one shape — this is what lets one graph serve both |
| `update_entity_status` | `(entity_type, entity_id, new_status)` | `EntityInfo` | `InvalidTransitionError` — calls `validate_transition`, so **no status write in the system can bypass the state machine** |
| `assign_approver` | `(entity_type, entity_id, approver_id)` | `EntityInfo` | `NotFoundError` |
| `get_user_details` | `(user_id)` | `UserInfo` | `NotFoundError` |
| `get_user_roles` | `(user_id)` | `list[RoleInfo]` | `NotFoundError` |
| `get_user_capabilities` | `(user_id)` | `list[str]` | Returns names (`["PURCHASE"]`) so the capability check is a plain membership test |
| `fetch_active_policies` | `(entity_type, capability)` | `list[PolicyInfo]` | Returns a **list by design**: more than one active policy is a conflict the evaluator must see, not a case to silently pick from (D3) |
| `get_approval_rules` | `(policy_id)` | `list[RuleInfo]` | Eager-loads the role (`joinedload`) to avoid an N+1; ordered by `priority`, then `min_value` |
| `find_eligible_approvers` | `(role_id, capability, exclude_user_id=None)` | `list[UserInfo]` | One query expressing four rules: active **and** holds the role **and** holds the capability **and** is not the creator; ordered by `id` for deterministic selection (D7) |
| `persist_audit_log` | `(entity_type, entity_id, action, *, execution_id, step_number, performed_by, agent_name, policy_id, rule_applied, tool_called, reasoning_trace, remarks)` | `int` (log id) | Keyword-only after `action` — eleven similarly-typed optionals would otherwise be a bug factory |

Which agent may call which is in the table above; an agent that requests a tool outside its toolkit raises `AgentProtocolError`.

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
                                  audit (persist trail, apply status)
                                    ├─ PENDING_APPROVAL → human_decision ──┐
                                    │        interrupt() + checkpoint      │
                                    │        ← resumed by the approver ────┘
                                    └─ otherwise ─────────────────────────→ END
```

One compiled graph serves both entity types; the request's DB session and chat model travel in LangGraph's `config["configurable"]`. Agent nodes are wrapped by `safe()` so an unexpected failure escalates with an audit entry instead of crashing mid-workflow. Two nodes are deliberately **not** wrapped: `audit`, because swallowing a trail-write failure would commit an unaudited decision, and `human_decision`, because `interrupt()` signals by raising and must reach the runtime.

### Shared state schema

The graph's memory is one Pydantic model, `ApprovalState` ([app/graph/state.py](app/graph/state.py)). Nodes never mutate it: each returns a **dict of the fields it changed**, and LangGraph merges that into the state before the next node runs — which is what makes the state checkpointable (D10) and every transition traceable.

| Field | Type | Written by | Purpose |
|---|---|---|---|
| `entity_type`, `entity_id` | `EntityType`, `int` | caller | Which transaction this run is about |
| `execution_id` | `str` | `default_factory=uuid4` | Groups this run's audit rows. Generated **by construction**, so no entry point can produce NULL-grouped rows |
| `creator_id`, `entity_value`, `capability` | `int`, `Decimal`, `CapabilityType` | caller, re-grounded by `load_entity` | The facts the policy is evaluated against |
| `creator_active`, `creator_role_ids`, `creator_role_names`, `creator_capabilities`, `capability_ok` | — | Context agent | Who the creator is and whether they may transact |
| `decision` | `PolicyDecision` | Policy agent | The governing rule, **plus** every matching rule — the two readings D2 depends on |
| `creator_authorized` | `bool` | `authority` node (deterministic) | Auto-approve or route |
| `approver_id` | `int \| None` | Routing agent | The assigned approver |
| `hitl` | `bool` | caller / settings | Whether to pause at `PENDING_APPROVAL`; kept in state so the branch is testable without globals |
| `final_status`, `status_reason`, `error` | — | any node | The outcome; `final_status` being set is what the conditional edges read to short-circuit |
| `steps_persisted` | `int` | audit node | How many rows were written, so a resumed run continues the numbering |
| `audit_entries` | `Annotated[list[AuditEntry], operator.add]` | every agent | **The one field with a reducer** |

That last row is the mechanism behind the audit trail. Every other field follows last-write-wins; `operator.add` makes returned lists **concatenate** instead of replacing, so four agents can each contribute one `AuditEntry` and the audit node finds all four. Without it, each node would overwrite the previous one's entry and the trail would be a single row.

`AuditEntry` is the per-step record an agent builds: `action`, `agent_name`, `policy_id`, `rule_applied`, `tool_called`, `reasoning_trace`, `remarks`.

Request-scoped dependencies — the `Session` and the chat model — deliberately travel in LangGraph's `config["configurable"]` rather than in the state, because the state gets serialised into checkpoints and a database session is neither serialisable nor meaningful after a restart.

Agent handoffs are separately typed: each LLM agent finishes by calling a `submit_*` tool whose arguments are a Pydantic model (`ContextResult`, `PolicyDecision`, `RoutingResult` in [app/schemas/](app/schemas/)), so the handoff is validated at the tool boundary before the node ever sees it.

### Entity state machine

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
├── database/                   engine/session, idempotent seed
└── exceptions.py               typed domain errors → HTTP status codes
tests/                          115 tests across 7 phases
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
One `ApprovalLog` row per agent action with structured fields (`agent_name`, `action`, `policy_id`, `rule_applied`, `tool_called`, `performed_by`, `execution_id`, `step_number`) **and** a concise natural-language `reasoning_trace` — never raw LLM chain-of-thought. `policy_id`, `tool_called`, `execution_id` and `step_number` extend the assignment's schema because its audit requirement demands the exact policy, the tool trail, and an ordered step-by-step trace of each workflow run. A human APPROVE/REJECT extends the run it resolves: with HITL enabled it becomes the next step of the *same* `execution_id` (D10), and on the fallback path it is recorded as its own single-step execution. Either way every row carries both fields, so a trail never mixes numbered and NULL steps.

### D7 — Approval action security (documented simplification: no auth system)
`POST /{entity}/{id}/action` takes `{user_id, action, remarks}`. Human actions are legal **only** from `PENDING_APPROVAL` (assigned approver) and `ESCALATED` (D8 authority) — any other status, including `CREATED`, returns 409, because `CREATED` belongs to the workflow and must never be human-decidable. The row is locked with `SELECT … FOR UPDATE` (PostgreSQL) and re-read fresh (`populate_existing`) for the whole check-and-transition, so concurrent duplicate actions serialize and the loser gets 409. Creators are excluded from eligibility (no self-approval); among multiple eligible approvers the lowest user id wins (deterministic). If the assigned approver has been **deactivated**, D8 authority holders may decide instead, so staff churn cannot deadlock an entity.

### D8 — Escalation / override authority
An entity in `ESCALATED` (or `PENDING_APPROVAL` with an inactive assigned approver) may be decided by any **active, non-creator** user who holds the entity's **capability** and the role of **any active rule matching the value** — in seed data that's Admin via the catch-all, with no hardcoded role. The predicate deliberately mirrors routing eligibility: nobody can resolve an entity they could never have been routed. An entity escalated for *no active policy* is resolvable again as soon as an active policy exists — restoring configuration restores authority, by data.

### D9 — Mock LLM runs the real graph
`MockChatModel` subclasses LangChain's `BaseChatModel` and deterministically scripts each agent's tool calls through the **identical** loop, tools, schemas and edges as a real model. Guardrails cross-check every LLM-produced id against the database in both modes.

### D10 — Human-in-the-loop by interrupt/resume
With `ENABLE_HITL=true` (the default) a `PENDING_APPROVAL` outcome does not end the run: the graph continues to a `human_decision` node that calls LangGraph's `interrupt()`, checkpointing the state. The status and the five audit rows are already committed, so the entity is durably pending while the run sleeps. The approver's `POST /{entity}/{id}/action` then **resumes that same run**, and the decision is recorded as step 6 of the *same* `execution_id` rather than a separate execution.

**Authorisation never moves into the graph.** `apply_action` still takes the row lock and runs every check (active actor, assigned approver or override authority, legal transition) *before* resuming; the `human_decision` node only applies the transition and extends the trail. A rejected actor leaves the run untouched and still parked.

Two deliberate properties: a finished run's checkpoint is **discarded** immediately, so only genuinely-paused runs hold memory; and if a checkpoint is missing — after a process restart, since the checkpointer is in-memory — `apply_action` falls back to applying the decision directly. The feature degrades to the pre-HITL behaviour instead of blocking approvals.

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

All 16 required cases are covered across `tests/` (115 tests): admin auto-approval (1), supervisor low/high value (2, 3), sales-exec low/high quote (4, 5), dual-capability user (6), no eligible approver (7), inactive policy (8), overlapping rules (9), priority conflicts (10), missing capability (11), unauthorized approval → 403 (12), invalid transitions → 409 (13), approver rejection (14), audit policy/rule ids (15), and mock mode with zero credentials (16 — the entire suite runs this way, plus explicit provider tests). Boundary values (9,999.99 / 10,000.00 / 25,000.00 / 25,000.01 / 100,000.00 / 100,000.01) are tested exactly.

## Scope notes

- **Migrations:** `Base.metadata.create_all` + idempotent seed instead of Alembic — a deliberate trade-off for the 8–12h scope; the models are Alembic-ready. Note `create_all` never ALTERs existing tables: after pulling a schema change, recreate the development database — delete the SQLite file, or `DROP`/`CREATE` the PostgreSQL one — and re-run the seed.
- **Tests on SQLite:** fast and service-free; `FOR UPDATE` is a no-op on SQLite and takes effect on PostgreSQL.
- **HITL checkpointer is in-memory.** Paused runs live in the process, so they do not survive a restart and are not shared across workers. The fallback path (D10) keeps approvals working regardless; a production deployment would swap in a persistent checkpointer (`langgraph-checkpoint-postgres`) — a one-line change, since `CHECKPOINTER` is the only binding.
- **Auth:** actor identity travels in the payload by design (D7); a real deployment would replace it with authenticated identity.
- **Session lifetime:** one session spans a workflow run so all writes (concentrated in the final audit node) commit atomically. With a real LLM this keeps a connection open for the run's duration — acceptable here; a production deployment would move graph execution to a worker queue and shorten read transactions.
- **Audit-write failures abort the run:** the audit node is deliberately outside the graph's error-fallback wrapper — a failure persisting the trail rolls back the whole run (5xx) rather than committing an unaudited decision.
- **Orphaned CREATED rows:** the entity is committed before the workflow runs, so a failure outside the graph (e.g. provider misconfiguration) leaves it `CREATED` with no trail and a 5xx response. Such rows accept no human action (409); re-run them via `process_entity` or remove them manually. A production system would enqueue the workflow in the same transaction (outbox pattern).
