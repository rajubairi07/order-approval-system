"""Domain exceptions shared by tools, services and (later) API handlers."""


class DomainError(Exception):
    """Base class for all domain errors."""


class NotFoundError(DomainError):
    """A referenced entity/user/policy does not exist."""


class InvalidTransitionError(DomainError):
    """A state transition not permitted by the state machine."""


class AuthorizationError(DomainError):
    """The acting user is not permitted to perform this action."""


class InvalidActionError(DomainError):
    """The requested action name is not a supported approval action."""
