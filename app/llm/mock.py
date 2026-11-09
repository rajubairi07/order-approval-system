"""Deterministic mock chat model (spec decision D9).

Implements LangChain's BaseChatModel so the agent tool-calling loop, tool
executors, Pydantic validation, and graph edges are IDENTICAL to real-LLM
mode. For each agent it follows a fixed script: call the agent's data tools
in order, read their results back from ToolMessages, then emit the submit_*
tool call with a decision computed from those results.

No API key, no network, fully reproducible.
"""

import json
import re
from decimal import Decimal
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.schemas.approval import PolicyInfo, RuleInfo
from app.services.evaluator import evaluate_policy

CONTEXT_MARKER = "CONTEXT_JSON:"


def _parse_conversation(messages: list[BaseMessage]):
    """Extract agent name, the context block, and executed tool calls."""
    agent_name = None
    context: dict[str, Any] = {}
    call_names: dict[str, tuple[str, dict]] = {}  # tool_call_id -> (name, args)
    executed: list[dict] = []  # {"name", "args", "result"} in execution order

    for msg in messages:
        if isinstance(msg, SystemMessage) and agent_name is None:
            match = re.search(r"You are the (\w+)", str(msg.content))
            agent_name = match.group(1) if match else None
        elif isinstance(msg, HumanMessage) and not context:
            text = str(msg.content)
            if CONTEXT_MARKER in text:
                context = json.loads(text.split(CONTEXT_MARKER, 1)[1])
        elif isinstance(msg, AIMessage):
            for tc in msg.tool_calls:
                call_names[tc["id"]] = (tc["name"], tc["args"])
        elif msg.type == "tool":
            name, args = call_names.get(msg.tool_call_id, (None, {}))
            executed.append(
                {"name": name, "args": args, "result": json.loads(str(msg.content))}
            )
    return agent_name, context, executed


def _results(executed: list[dict], name: str) -> list[dict]:
    return [e for e in executed if e["name"] == name]


class MockChatModel(BaseChatModel):
    """Scripted, deterministic stand-in for a tool-calling chat model."""

    @property
    def _llm_type(self) -> str:
        return "mock-deterministic"

    def bind_tools(self, tools, **kwargs):  # scripts already know their tools
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        agent_name, context, executed = _parse_conversation(messages)
        script = {
            "ContextCapabilityAgent": self._context_script,
            "PolicyEvaluationAgent": self._policy_script,
            "RoutingWorkflowAgent": self._routing_script,
        }.get(agent_name)
        if script is None:
            raise ValueError(f"MockChatModel has no script for agent '{agent_name}'")
        name, args = script(context, executed)
        ai = AIMessage(
            content="",
            tool_calls=[{"name": name, "args": args, "id": f"mock_call_{len(messages)}"}],
        )
        return ChatResult(generations=[ChatGeneration(message=ai)])

    # --- agent scripts -------------------------------------------------

    def _context_script(self, ctx, executed):
        if not _results(executed, "get_entity_details"):
            return "get_entity_details", {
                "entity_type": ctx["entity_type"],
                "entity_id": ctx["entity_id"],
            }
        if not _results(executed, "get_user_details"):
            return "get_user_details", {"user_id": ctx["creator_id"]}
        if not _results(executed, "get_user_roles"):
            return "get_user_roles", {"user_id": ctx["creator_id"]}
        if not _results(executed, "get_user_capabilities"):
            return "get_user_capabilities", {"user_id": ctx["creator_id"]}

        user = _results(executed, "get_user_details")[0]["result"]
        roles = _results(executed, "get_user_roles")[0]["result"]
        caps = _results(executed, "get_user_capabilities")[0]["result"]
        capability_ok = ctx["capability"] in caps and user["is_active"]
        return "submit_context", {
            "creator_active": user["is_active"],
            "creator_role_ids": [r["id"] for r in roles],
            "creator_role_names": [r["name"] for r in roles],
            "creator_capabilities": caps,
            "capability_ok": capability_ok,
            "explanation": (
                f"Creator {user['name']} (active={user['is_active']}) holds roles "
                f"{[r['name'] for r in roles]} and capabilities {caps}; required "
                f"capability {ctx['capability']} is "
                f"{'present' if capability_ok else 'missing or unusable'}."
            ),
        }

    def _policy_script(self, ctx, executed):
        policy_fetches = _results(executed, "fetch_active_policies")
        if not policy_fetches:
            return "fetch_active_policies", {
                "entity_type": ctx["entity_type"],
                "capability": ctx["capability"],
            }
        policies = [PolicyInfo.model_validate(p) for p in policy_fetches[0]["result"]]
        rules_by_policy = {
            e["args"]["policy_id"]: [RuleInfo.model_validate(r) for r in e["result"]]
            for e in _results(executed, "get_approval_rules")
        }
        for policy in policies:
            if policy.id not in rules_by_policy:
                return "get_approval_rules", {"policy_id": policy.id}

        decision = evaluate_policy(policies, rules_by_policy, Decimal(ctx["entity_value"]))
        return "submit_policy_decision", decision.model_dump(mode="json")

    def _routing_script(self, ctx, executed):
        finds = _results(executed, "find_eligible_approvers")
        if not finds:
            return "find_eligible_approvers", {
                "role_id": ctx["required_role_id"],
                "capability": ctx["capability"],
                "exclude_user_id": ctx["creator_id"],
            }
        approvers = finds[0]["result"]
        if approvers:
            chosen = approvers[0]
            return "submit_routing", {
                "approver_id": chosen["id"],
                "explanation": (
                    f"Assigned approver {chosen['name']} (id {chosen['id']}), the "
                    f"first of {len(approvers)} active eligible holder(s) of the "
                    f"required role with the {ctx['capability']} capability."
                ),
            }
        return "submit_routing", {
            "approver_id": None,
            "explanation": (
                "No active user holds the required role with the "
                f"{ctx['capability']} capability; escalating."
            ),
        }
