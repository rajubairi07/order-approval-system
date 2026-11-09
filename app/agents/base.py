"""Generic agent tool-calling loop, shared by all LLM-backed agents.

The loop is identical in real and mock mode (spec decision D9): the chat
model requests tool calls, we execute them against the database, feed results
back, and stop when the agent calls its submit_* tool with its structured
output. The submit args are then Pydantic-validated and deterministically
cross-checked by the calling node.
"""

import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from app.exceptions import DomainError

CONTEXT_MARKER = "CONTEXT_JSON:"
MAX_TURNS = 10


class AgentProtocolError(DomainError):
    """The agent did not follow the tool-calling protocol."""


def run_agent_loop(
    llm: BaseChatModel,
    tools: list[StructuredTool],
    submit_tool: StructuredTool,
    system_prompt: str,
    context: dict,
) -> tuple[dict, list[str]]:
    """Drive one agent to completion; returns (submitted args, tool trace)."""
    llm_bound = llm.bind_tools([*tools, submit_tool])
    tool_map = {t.name: t for t in tools}
    messages = [
        SystemMessage(system_prompt),
        HumanMessage(
            "Perform your task for the transaction below, using your tools for all "
            f"authoritative data.\n{CONTEXT_MARKER}{json.dumps(context)}"
        ),
    ]
    trace: list[str] = []

    for _ in range(MAX_TURNS):
        ai = llm_bound.invoke(messages)
        messages.append(ai)
        if not ai.tool_calls:
            raise AgentProtocolError(
                f"Agent produced no tool call; expected data tools then {submit_tool.name}."
            )
        for tc in ai.tool_calls:
            if tc["name"] == submit_tool.name:
                return tc["args"], trace
            if tc["name"] not in tool_map:
                raise AgentProtocolError(f"Agent called unknown tool '{tc['name']}'")
            result = tool_map[tc["name"]].invoke(tc["args"])
            trace.append(tc["name"])
            messages.append(
                ToolMessage(content=json.dumps(result, default=str), tool_call_id=tc["id"])
            )

    raise AgentProtocolError(f"Agent exceeded {MAX_TURNS} turns without submitting.")
