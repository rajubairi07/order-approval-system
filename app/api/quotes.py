from app.api.entity_router import build_entity_router
from app.models.enums import EntityType
from app.schemas.quote import QuoteCreate, QuoteResponse

router = build_entity_router(
    prefix="/quotes",
    entity_type=EntityType.QUOTE,
    create_schema=QuoteCreate,
    response_schema=QuoteResponse,
    value_field="quote_value",
)
