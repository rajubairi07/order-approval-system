import enum


class EntityType(str, enum.Enum):
    ORDER = "ORDER"
    QUOTE = "QUOTE"


class CapabilityType(str, enum.Enum):
    PURCHASE = "PURCHASE"
    SALES = "SALES"


class EntityStatus(str, enum.Enum):
    CREATED = "CREATED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"
