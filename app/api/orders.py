from app.api.entity_router import build_entity_router
from app.models.enums import EntityType
from app.schemas.order import OrderCreate, OrderResponse

router = build_entity_router(
    prefix="/orders",
    entity_type=EntityType.ORDER,
    create_schema=OrderCreate,
    response_schema=OrderResponse,
    value_field="order_value",
)
