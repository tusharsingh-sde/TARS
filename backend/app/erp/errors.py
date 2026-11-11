class ERPError(Exception):
    """Base domain exception for ERP repository operations."""


class ERPNotFoundError(ERPError):
    """Raised when a requested ERP entity does not exist."""


class ERPInvalidStateError(ERPError):
    """Raised when an operation is invalid for the entity state or input."""

