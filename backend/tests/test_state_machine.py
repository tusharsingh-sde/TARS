import pytest

from app.agent.state import (
    ALLOWED_TRANSITIONS,
    InvalidOrderTransitionError,
    transition_order,
)
from app.erp.models import OrderStatus


@pytest.mark.parametrize(
    ("current_status", "next_status"),
    [
        (OrderStatus.DRAFT, OrderStatus.CONFIRMING),
        (OrderStatus.DRAFT, OrderStatus.PENDING_APPROVAL),
        (OrderStatus.DRAFT, OrderStatus.REJECTED),
        (OrderStatus.CONFIRMING, OrderStatus.DRAFT),
        (OrderStatus.CONFIRMING, OrderStatus.REJECTED),
        (OrderStatus.PENDING_APPROVAL, OrderStatus.CONFIRMING),
        (OrderStatus.PENDING_APPROVAL, OrderStatus.REJECTED),
    ],
)
def test_allowed_non_commit_transitions(
    current_status: OrderStatus,
    next_status: OrderStatus,
) -> None:
    assert transition_order(current_status, next_status) == next_status


def test_commit_requires_explicit_voice_confirmation() -> None:
    with pytest.raises(InvalidOrderTransitionError, match="voice confirmation"):
        transition_order(OrderStatus.CONFIRMING, OrderStatus.COMMITTED, confirmed_by_voice=False)

    assert (
        transition_order(OrderStatus.CONFIRMING, OrderStatus.COMMITTED, confirmed_by_voice=True)
        == OrderStatus.COMMITTED
    )


@pytest.mark.parametrize("confirmed_by_voice", [False, True])
def test_all_disallowed_transitions_raise(confirmed_by_voice: bool) -> None:
    for current_status in OrderStatus:
        allowed_targets = ALLOWED_TRANSITIONS[current_status]
        for next_status in OrderStatus:
            if next_status in allowed_targets:
                continue
            with pytest.raises(InvalidOrderTransitionError):
                transition_order(
                    current_status,
                    next_status,
                    confirmed_by_voice=confirmed_by_voice,
                )


def test_terminal_states_have_no_outgoing_transitions() -> None:
    assert ALLOWED_TRANSITIONS[OrderStatus.COMMITTED] == frozenset()
    assert ALLOWED_TRANSITIONS[OrderStatus.REJECTED] == frozenset()


def test_realistic_order_lifecycle_paths() -> None:
    # Clean order with mid-read-back spoken edit
    status = OrderStatus.DRAFT
    status = transition_order(status, OrderStatus.CONFIRMING)
    status = transition_order(status, OrderStatus.DRAFT)
    status = transition_order(status, OrderStatus.CONFIRMING)
    status = transition_order(status, OrderStatus.COMMITTED, confirmed_by_voice=True)
    assert status == OrderStatus.COMMITTED

    # Approval breach approved by manager, then committed after voice confirmation
    status = OrderStatus.DRAFT
    status = transition_order(status, OrderStatus.PENDING_APPROVAL)
    status = transition_order(status, OrderStatus.CONFIRMING)
    status = transition_order(status, OrderStatus.COMMITTED, confirmed_by_voice=True)
    assert status == OrderStatus.COMMITTED

    # Approval breach rejected by manager
    status = transition_order(OrderStatus.DRAFT, OrderStatus.PENDING_APPROVAL)
    status = transition_order(status, OrderStatus.REJECTED)
    assert status == OrderStatus.REJECTED

