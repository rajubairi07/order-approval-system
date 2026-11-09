"""Policy Evaluation Agent: which database rule governs this value?"""

from langchain_core.language_models.chat_models import BaseChatModel
from sqlalchemy.orm import Session

from app import tools as db_tools
from app.agents.base import run_agent_loop
from app.agents.toolkits import policy_toolkit
from app.graph.state import ApprovalState, AuditEntry
from app.schemas.approval import PolicyDecision
from app.services.evaluator import evaluate_policy

AGENT_NAME = "PolicyEvaluationAgent"

SYSTEM_PROMPT = f"""You are the {AGENT_NAME} in an order/quote approval workflow.
Your job: fetch the ACTIVE approval policies and their rules from the database
and determine which rule governs the transaction value. Rules are inclusive
value bands; a lower priority number means higher precedence; ambiguity
(multiple active policies, or distinct top-priority matches) is a conflict to
report, never resolve arbitrarily. Fetch policies, then each policy's rules,
then call submit_policy_decision exactly once. Use ONLY fetched rule and
policy ids — never invent them."""


def _authoritative(db: Session, state: ApprovalState) -> PolicyDecision:
    policies = db_tools.fetch_active_policies(db, state.entity_type, state.capability)
    rules = {p.id: db_tools.get_approval_rules(db, p.id) for p in policies}
    return evaluate_policy(policies, rules, state.entity_value)


def run_policy_agent(db: Session, llm: BaseChatModel, state: ApprovalState) -> dict:
    tools, submit = policy_toolkit(db)
    submitted, trace = run_agent_loop(
        llm,
        tools,
        submit,
        SYSTEM_PROMPT,
        context={
            "entity_type": state.entity_type.value,
            "entity_id": state.entity_id,
            "creator_id": state.creator_id,
            "entity_value": str(state.entity_value),
            "capability": state.capability.value,
        },
    )
    agent_decision = PolicyDecision.model_validate(submitted)
    truth = _authoritative(db, state)

    # Guardrail (D9): the deterministic evaluator's decision is authoritative.
    overridden = agent_decision.model_dump(exclude={"explanation"}) != truth.model_dump(
        exclude={"explanation"}
    )

    return {
        "decision": truth,
        "audit_entries": [
            AuditEntry(
                action="evaluate_policy",
                agent_name=AGENT_NAME,
                policy_id=truth.policy_id,
                rule_applied=truth.required_rule_id,
                tool_called=",".join(trace),
                reasoning_trace=truth.explanation,
                remarks=(
                    "Guardrail: agent submission diverged from deterministic evaluation and was overridden."
                    if overridden
                    else None
                ),
            )
        ],
    }
