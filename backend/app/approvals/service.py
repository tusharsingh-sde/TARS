from sqlalchemy.orm import Session

from app.agent.state import transition_order
from app.erp import repo
from app.erp.errors import ERPNotFoundError
from app.erp.models import ApprovalStatus, OrderStatus
from app.erp.schemas import ApprovalDTO, OrderDTO


def submit_approval_request(
    session: Session,
    order_id: str,
    reason_code: str,
) -> ApprovalDTO:
    """Transition an order to PENDING_APPROVAL and create a manager approval request."""
    order = repo.get_order(session, order_id)
    if order is None:
        raise ERPNotFoundError(f"Order '{order_id}' not found")

    next_status = transition_order(order.status, OrderStatus.PENDING_APPROVAL)
    repo.set_order_status(session, order_id, next_status)
    approval = repo.create_approval_request(session, order_id, reason_code)

    repo.append_audit_event(
        session,
        order_id,
        event_type="APPROVAL_REQUESTED",
        payload={
            "request_id": approval.request_id,
            "reason_code": approval.reason_code,
            "total_paise": order.total_paise,
        },
    )
    return approval


def resolve_approval_request(
    session: Session,
    request_id: str,
    approved: bool,
    decided_by: str,
) -> tuple[ApprovalDTO, OrderDTO]:
    """Record a human manager decision and transition the associated order."""
    decision = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
    approval = repo.decide_approval(session, request_id, decision, decided_by)

    order = repo.get_order(session, approval.order_id)
    if order is None:
        raise ERPNotFoundError(f"Order '{approval.order_id}' not found")

    target_status = OrderStatus.CONFIRMING if approved else OrderStatus.REJECTED
    next_status = transition_order(order.status, target_status)
    updated_order = repo.set_order_status(session, order.order_id, next_status)

    event_type = "APPROVAL_APPROVED" if approved else "APPROVAL_REJECTED"
    repo.append_audit_event(
        session,
        order.order_id,
        event_type=event_type,
        payload={
            "request_id": approval.request_id,
            "decided_by": approval.decided_by or decided_by.strip(),
            "reason_code": approval.reason_code,
        },
    )
    return approval, updated_order

