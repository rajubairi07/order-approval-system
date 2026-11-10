"""Workflow entry point: run, pause and resume the shared approval graph."""

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.types import Command
from sqlalchemy.orm import Session

from app import tools as db_tools
from app.config import get_settings
from app.graph.state import ApprovalState
from app.graph.workflow import discard_thread, get_workflow, thread_id
from app.llm.provider import get_chat_model
from app.models.enums import CapabilityType, EntityType

#: Which capability each entity type transacts under (assignment mapping).
CAPABILITY_FOR_ENTITY = {
    EntityType.ORDER: CapabilityType.PURCHASE,
    EntityType.QUOTE: CapabilityType.SALES,
}


def _config(db: Session, llm: BaseChatModel, entity_type: EntityType, entity_id: int) -> dict:
    return {
        "configurable": {
            "db": db,
            "llm": llm,
            "thread_id": thread_id(entity_type, entity_id),
        }
    }


def process_entity(
    db: Session,
    entity_type: EntityType,
    entity_id: int,
    llm: BaseChatModel | None = None,
    hitl: bool | None = None,
) -> ApprovalState:
    """Run the approval workflow; commits on success, rolls back on failure.

    With human-in-the-loop enabled, a PENDING_APPROVAL outcome leaves the run
    *interrupted* rather than finished: the status and audit trail are already
    persisted, and the approver's action later resumes it.
    """
    llm = llm or get_chat_model()
    if hitl is None:
        hitl = get_settings().enable_hitl

    info = db_tools.get_entity_details(db, entity_type, entity_id)
    initial = ApprovalState(
        entity_type=entity_type,
        entity_id=entity_id,
        creator_id=info.created_by,
        entity_value=info.value,
        capability=CAPABILITY_FOR_ENTITY[entity_type],
        hitl=hitl,
    )
    try:
        raw = get_workflow().invoke(
            initial, config=_config(db, llm, entity_type, entity_id)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    # An interrupted run returns the accumulated state plus __interrupt__,
    # which ApprovalState ignores as an unknown field.
    if "__interrupt__" not in raw:
        discard_thread(entity_type, entity_id)
    return ApprovalState.model_validate(raw)


def awaiting_human_decision(entity_type: EntityType, entity_id: int) -> bool:
    """True when this entity has a run paused at the human_decision node."""
    snapshot = get_workflow().get_state(
        {"configurable": {"thread_id": thread_id(entity_type, entity_id)}}
    )
    return "human_decision" in (snapshot.next or ())


def resume_entity(
    db: Session,
    entity_type: EntityType,
    entity_id: int,
    decision: dict,
    llm: BaseChatModel | None = None,
) -> ApprovalState:
    """Resume a paused run with the approver's decision.

    The caller (`apply_action`) has already locked the row and verified that
    this actor may decide: authorisation never moves into the graph.
    """
    llm = llm or get_chat_model()
    try:
        raw = get_workflow().invoke(
            Command(resume=decision),
            config=_config(db, llm, entity_type, entity_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    discard_thread(entity_type, entity_id)
    return ApprovalState.model_validate(raw)
