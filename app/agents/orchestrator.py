"""Workflow entry point: run the shared approval graph for one entity."""

from langchain_core.language_models.chat_models import BaseChatModel
from sqlalchemy.orm import Session

from app import tools as db_tools
from app.graph.state import ApprovalState
from app.graph.workflow import get_workflow
from app.llm.provider import get_chat_model
from app.models.enums import CapabilityType, EntityType

#: Which capability each entity type transacts under (assignment mapping).
CAPABILITY_FOR_ENTITY = {
    EntityType.ORDER: CapabilityType.PURCHASE,
    EntityType.QUOTE: CapabilityType.SALES,
}


def process_entity(
    db: Session,
    entity_type: EntityType,
    entity_id: int,
    llm: BaseChatModel | None = None,
) -> ApprovalState:
    """Run the approval workflow; commits on success, rolls back on failure."""
    llm = llm or get_chat_model()
    info = db_tools.get_entity_details(db, entity_type, entity_id)
    initial = ApprovalState(
        entity_type=entity_type,
        entity_id=entity_id,
        creator_id=info.created_by,
        entity_value=info.value,
        capability=CAPABILITY_FOR_ENTITY[entity_type],
    )
    try:
        raw = get_workflow().invoke(
            initial, config={"configurable": {"db": db, "llm": llm}}
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ApprovalState.model_validate(raw)
