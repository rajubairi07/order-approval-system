"""LangChain tool bindings over the database tools, per agent.

Tools are constructed per run with the request's Session closed over, so
agents can only touch the database through these typed, described tools
(assignment's grounding requirement).
"""

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool
from sqlalchemy.orm import Session

from app import tools as db_tools
from app.models.enums import CapabilityType, EntityType
from app.schemas.agents import ContextResult, RoutingResult
from app.schemas.approval import PolicyDecision


class EntityRef(BaseModel):
    entity_type: EntityType = Field(description="ORDER or QUOTE")
    entity_id: int = Field(description="Primary key of the order/quote")


class UserRef(BaseModel):
    user_id: int = Field(description="Primary key of the user")


class PolicyQuery(BaseModel):
    entity_type: EntityType = Field(description="ORDER or QUOTE")
    capability: CapabilityType = Field(description="PURCHASE or SALES")


class PolicyRef(BaseModel):
    policy_id: int = Field(description="Primary key of the approval policy")


class ApproverQuery(BaseModel):
    role_id: int = Field(description="Role id the approver must hold")
    capability: CapabilityType = Field(description="Capability the approver must hold")
    exclude_user_id: int | None = Field(
        default=None, description="User id to exclude (the creator; no self-approval)"
    )


def _submit_tool(name: str, description: str, schema: type[BaseModel]) -> StructuredTool:
    """A terminal tool: intercepted by the loop, never executed."""
    return StructuredTool.from_function(
        func=lambda **kwargs: "submitted",
        name=name,
        description=description,
        args_schema=schema,
    )


def context_toolkit(db: Session) -> tuple[list[StructuredTool], StructuredTool]:
    tools = [
        StructuredTool.from_function(
            func=lambda entity_type, entity_id: db_tools.get_entity_details(
                db, entity_type, entity_id
            ).model_dump(mode="json"),
            name="get_entity_details",
            description="Fetch the order/quote: creator, value, status, approver.",
            args_schema=EntityRef,
        ),
        StructuredTool.from_function(
            func=lambda user_id: db_tools.get_user_details(db, user_id).model_dump(),
            name="get_user_details",
            description="Fetch a user's name, email and active flag.",
            args_schema=UserRef,
        ),
        StructuredTool.from_function(
            func=lambda user_id: [r.model_dump() for r in db_tools.get_user_roles(db, user_id)],
            name="get_user_roles",
            description="Fetch all roles (id, name) held by a user.",
            args_schema=UserRef,
        ),
        StructuredTool.from_function(
            func=lambda user_id: db_tools.get_user_capabilities(db, user_id),
            name="get_user_capabilities",
            description="Fetch capability names (PURCHASE/SALES) held by a user.",
            args_schema=UserRef,
        ),
    ]
    submit = _submit_tool(
        "submit_context",
        "Submit your validated context result. Call exactly once, after the data tools.",
        ContextResult,
    )
    return tools, submit


def policy_toolkit(db: Session) -> tuple[list[StructuredTool], StructuredTool]:
    tools = [
        StructuredTool.from_function(
            func=lambda entity_type, capability: [
                p.model_dump(mode="json")
                for p in db_tools.fetch_active_policies(db, entity_type, capability)
            ],
            name="fetch_active_policies",
            description="Fetch ACTIVE approval policies for an entity type + capability.",
            args_schema=PolicyQuery,
        ),
        StructuredTool.from_function(
            func=lambda policy_id: [
                r.model_dump(mode="json") for r in db_tools.get_approval_rules(db, policy_id)
            ],
            name="get_approval_rules",
            description="Fetch a policy's rules: value bands, allowed role, priority.",
            args_schema=PolicyRef,
        ),
    ]
    submit = _submit_tool(
        "submit_policy_decision",
        "Submit the policy decision derived ONLY from fetched rules. Call exactly once.",
        PolicyDecision,
    )
    return tools, submit


def routing_toolkit(db: Session) -> tuple[list[StructuredTool], StructuredTool]:
    tools = [
        StructuredTool.from_function(
            func=lambda role_id, capability, exclude_user_id=None: [
                u.model_dump()
                for u in db_tools.find_eligible_approvers(
                    db, role_id, capability, exclude_user_id
                )
            ],
            name="find_eligible_approvers",
            description=(
                "Find active users holding the required role AND capability, ordered "
                "by user id; the creator is excluded via exclude_user_id."
            ),
            args_schema=ApproverQuery,
        ),
    ]
    submit = _submit_tool(
        "submit_routing",
        "Submit the chosen approver id (or null when none exists). Call exactly once.",
        RoutingResult,
    )
    return tools, submit
