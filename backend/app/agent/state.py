from typing import Final

from app.erp.models import OrderStatus


class InvalidOrderTransitionError(ValueError):
    """Raised when an order status transition violates state machine rules."""


ALLOWED_TRANSITIONS: Final[dict[OrderStatus, frozenset[OrderStatus]]] = {
    OrderStatus.DRAFT: frozenset({
        OrderStatus.CONFIRMING,
        OrderStatus.PENDING_APPROVAL,
        OrderStatus.REJECTED,
    }),
    OrderStatus.CONFIRMING: frozenset({
        OrderStatus.DRAFT,
        OrderStatus.COMMITTED,
        OrderStatus.REJECTED,
    }),
    OrderStatus.PENDING_APPROVAL: frozenset({
        OrderStatus.CONFIRMING,
        OrderStatus.REJECTED,
    }),
    OrderStatus.COMMITTED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
}


def transition_order(
    current_status: OrderStatus,
    next_status: OrderStatus,
    confirmed_by_voice: bool = False,
) -> OrderStatus:
    """Validate and return the next order status, enforcing voice confirmation on commit."""
    allowed_next = ALLOWED_TRANSITIONS.get(current_status, frozenset())
    if next_status not in allowed_next:
        raise InvalidOrderTransitionError(
            f"Illegal transition from {current_status.value} to {next_status.value}"
        )

    if next_status == OrderStatus.COMMITTED and not confirmed_by_voice:
        raise InvalidOrderTransitionError(
            "Cannot commit order without explicit voice confirmation"
        )

    return next_status

