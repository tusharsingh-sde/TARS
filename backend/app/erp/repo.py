import json
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import InstrumentedAttribute, Session, selectinload

from app.erp.errors import ERPError, ERPInvalidStateError, ERPNotFoundError
from app.erp.models import (
    ApprovalRequest,
    ApprovalStatus,
    AuditEvent,
    Invoice,
    Order,
    OrderLine,
    OrderStatus,
    Outlet,
    OutletAlias,
    SKU,
    SKUAlias,
    Scheme,
    utc_now,
)
from app.erp.schemas import (
    ApprovalDTO,
    AuditEventDTO,
    InvoiceDTO,
    OrderDTO,
    OrderLineDTO,
    OrderLineInput,
    OutletDTO,
    SchemeDTO,
    SkuDTO,
)


def _flush(session: Session) -> None:
    try:
        session.flush()
    except SQLAlchemyError as exc:
        raise ERPError(f"Database operation failed: {exc}") from exc


def _outlet_to_dto(outlet: Outlet) -> OutletDTO:
    return OutletDTO(
        outlet_id=outlet.outlet_id,
        name=outlet.name,
        owner_name=outlet.owner_name,
        locality=outlet.locality,
        city=outlet.city,
        route_code=outlet.route_code,
        credit_limit_paise=outlet.credit_limit_paise,
        outstanding_paise=outlet.outstanding_paise,
        aliases=sorted(item.alias for item in outlet.aliases),
    )


def _sku_to_dto(sku: SKU) -> SkuDTO:
    return SkuDTO(
        sku_id=sku.sku_id,
        brand=sku.brand,
        product=sku.product,
        pack_size=sku.pack_size,
        unit=sku.unit,
        mrp_paise=sku.mrp_paise,
        base_price_paise=sku.base_price_paise,
        stock_units=sku.stock_units,
        aliases=sorted(item.alias for item in sku.aliases),
    )


def _order_to_dto(order: Order) -> OrderDTO:
    return OrderDTO(
        order_id=order.order_id,
        outlet_id=order.outlet_id,
        rep_id=order.rep_id,
        status=order.status,
        total_paise=order.total_paise,
        lines=[OrderLineDTO.model_validate(line) for line in order.lines],
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


def _next_sequence_id(
    session: Session,
    column: InstrumentedAttribute[str],
    prefix: str,
    width: int,
) -> str:
    latest = session.scalar(
        select(column).where(column.like(f"{prefix}%")).order_by(column.desc()).limit(1)
    )
    next_num = 1
    if isinstance(latest, str) and latest.startswith(prefix):
        suffix = latest[len(prefix) :]
        if suffix.isdigit():
            next_num = int(suffix) + 1
    return f"{prefix}{next_num:0{width}d}"


def _require_order(session: Session, order_id: str) -> Order:
    stmt = (
        select(Order)
        .where(Order.order_id == order_id)
        .options(selectinload(Order.lines))
    )
    order = session.scalar(stmt)
    if order is None:
        raise ERPNotFoundError(f"Order '{order_id}' not found")
    return order


def get_outlet(session: Session, outlet_id: str) -> OutletDTO | None:
    stmt = (
        select(Outlet)
        .where(Outlet.outlet_id == outlet_id)
        .options(selectinload(Outlet.aliases))
    )
    outlet = session.scalar(stmt)
    return _outlet_to_dto(outlet) if outlet is not None else None


def list_outlets(session: Session) -> list[OutletDTO]:
    stmt = select(Outlet).options(selectinload(Outlet.aliases)).order_by(Outlet.outlet_id)
    return [_outlet_to_dto(item) for item in session.scalars(stmt).all()]


def find_outlet_by_alias(session: Session, alias_normalized: str) -> list[OutletDTO]:
    normalized = alias_normalized.strip().lower()
    if not normalized:
        return []
    stmt = (
        select(Outlet)
        .join(OutletAlias, Outlet.outlet_id == OutletAlias.outlet_id)
        .where(func.lower(OutletAlias.alias) == normalized)
        .options(selectinload(Outlet.aliases))
        .order_by(Outlet.outlet_id)
        .distinct()
    )
    return [_outlet_to_dto(item) for item in session.scalars(stmt).all()]


def get_sku(session: Session, sku_id: str) -> SkuDTO | None:
    stmt = select(SKU).where(SKU.sku_id == sku_id).options(selectinload(SKU.aliases))
    sku = session.scalar(stmt)
    return _sku_to_dto(sku) if sku is not None else None


def list_skus(session: Session) -> list[SkuDTO]:
    stmt = select(SKU).options(selectinload(SKU.aliases)).order_by(SKU.sku_id)
    return [_sku_to_dto(item) for item in session.scalars(stmt).all()]


def find_sku_by_alias(session: Session, alias_normalized: str) -> list[SkuDTO]:
    normalized = alias_normalized.strip().lower()
    if not normalized:
        return []
    stmt = (
        select(SKU)
        .join(SKUAlias, SKU.sku_id == SKUAlias.sku_id)
        .where(func.lower(SKUAlias.alias) == normalized)
        .options(selectinload(SKU.aliases))
        .order_by(SKU.sku_id)
        .distinct()
    )
    return [_sku_to_dto(item) for item in session.scalars(stmt).all()]


def get_active_schemes(session: Session, sku_id: str, on_date: date) -> list[SchemeDTO]:
    stmt = (
        select(Scheme)
        .where(
            Scheme.sku_id == sku_id,
            Scheme.valid_from <= on_date,
            Scheme.valid_to >= on_date,
        )
        .order_by(Scheme.scheme_id)
    )
    return [SchemeDTO.model_validate(item) for item in session.scalars(stmt).all()]


def create_order_draft(session: Session, outlet_id: str, rep_id: str) -> OrderDTO:
    outlet = session.get(Outlet, outlet_id)
    if outlet is None:
        raise ERPNotFoundError(f"Outlet '{outlet_id}' not found")
    if not rep_id.strip():
        raise ERPInvalidStateError("rep_id must not be empty")

    date_prefix = f"ORD-{utc_now().strftime('%Y%m%d')}-"
    order_id = _next_sequence_id(session, Order.order_id, date_prefix, width=4)
    order = Order(
        order_id=order_id,
        outlet_id=outlet_id,
        rep_id=rep_id.strip(),
        status=OrderStatus.DRAFT,
        total_paise=0,
    )
    session.add(order)
    _flush(session)
    session.refresh(order)
    return _order_to_dto(order)


def set_order_lines(
    session: Session,
    order_id: str,
    lines: list[OrderLineInput],
) -> OrderDTO:
    order = _require_order(session, order_id)
    requested_sku_ids = {item.sku_id for item in lines}
    if requested_sku_ids:
        found_sku_ids = set(
            session.scalars(select(SKU.sku_id).where(SKU.sku_id.in_(requested_sku_ids))).all()
        )
        missing = sorted(requested_sku_ids - found_sku_ids)
        if missing:
            raise ERPNotFoundError(f"Unknown SKU(s): {', '.join(missing)}")

    order.lines.clear()
    _flush(session)

    for line in lines:
        order.lines.append(
            OrderLine(
                sku_id=line.sku_id,
                quantity=line.quantity,
                unit_price_paise=line.unit_price_paise,
                discount_bps=line.discount_bps,
                line_total_paise=line.line_total_paise,
            )
        )
    order.updated_at = utc_now()
    _flush(session)
    session.refresh(order)
    return _order_to_dto(order)


def set_order_status(
    session: Session,
    order_id: str,
    new_status: OrderStatus | str,
) -> OrderDTO:
    order = _require_order(session, order_id)
    try:
        target_status = OrderStatus(new_status)
    except ValueError as exc:
        raise ERPInvalidStateError(f"Invalid order status '{new_status}'") from exc

    order.status = target_status
    order.updated_at = utc_now()
    _flush(session)
    session.refresh(order)
    return _order_to_dto(order)


def create_invoice(session: Session, order_id: str) -> InvoiceDTO:
    order = _require_order(session, order_id)
    if order.status != OrderStatus.COMMITTED:
        raise ERPInvalidStateError(
            f"Cannot create invoice for order '{order_id}' with status {order.status}"
        )

    existing = session.scalar(select(Invoice).where(Invoice.order_id == order_id))
    if existing is not None:
        raise ERPInvalidStateError(f"Invoice already exists for order '{order_id}'")

    invoice_id = _next_sequence_id(session, Invoice.invoice_id, "INV-", width=6)
    invoice = Invoice(
        invoice_id=invoice_id,
        order_id=order.order_id,
        total_paise=order.total_paise,
    )
    session.add(invoice)
    _flush(session)
    session.refresh(invoice)
    return InvoiceDTO.model_validate(invoice)


def create_approval_request(
    session: Session,
    order_id: str,
    reason_code: str,
) -> ApprovalDTO:
    _require_order(session, order_id)
    if not reason_code.strip():
        raise ERPInvalidStateError("reason_code must not be empty")

    request_id = _next_sequence_id(session, ApprovalRequest.request_id, "APR-", width=6)
    req = ApprovalRequest(
        request_id=request_id,
        order_id=order_id,
        reason_code=reason_code.strip(),
        status=ApprovalStatus.PENDING,
    )
    session.add(req)
    _flush(session)
    session.refresh(req)
    return ApprovalDTO.model_validate(req)


def decide_approval(
    session: Session,
    request_id: str,
    decision: ApprovalStatus | str,
    decided_by: str,
) -> ApprovalDTO:
    req = session.get(ApprovalRequest, request_id)
    if req is None:
        raise ERPNotFoundError(f"Approval request '{request_id}' not found")
    if req.status != ApprovalStatus.PENDING:
        raise ERPInvalidStateError(
            f"Approval request '{request_id}' already decided with status {req.status}"
        )

    try:
        target_decision = ApprovalStatus(decision)
    except ValueError as exc:
        raise ERPInvalidStateError(f"Invalid approval decision '{decision}'") from exc

    if target_decision == ApprovalStatus.PENDING:
        raise ERPInvalidStateError("Decision must be APPROVED or REJECTED")
    if not decided_by.strip():
        raise ERPInvalidStateError("decided_by must not be empty")

    req.status = target_decision
    req.decided_by = decided_by.strip()
    req.decided_at = utc_now()
    _flush(session)
    session.refresh(req)
    return ApprovalDTO.model_validate(req)


def append_audit_event(
    session: Session,
    order_id: str | None,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    if order_id is not None:
        _require_order(session, order_id)
    if not event_type.strip():
        raise ERPInvalidStateError("event_type must not be empty")

    try:
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    except TypeError as exc:
        raise ERPInvalidStateError(f"Audit payload is not JSON-serializable: {exc}") from exc

    event = AuditEvent(
        order_id=order_id,
        event_type=event_type.strip(),
        payload_json=payload_json,
    )
    session.add(event)
    _flush(session)


def get_order(session: Session, order_id: str) -> OrderDTO | None:
    """Return an OrderDTO with eager-loaded lines, or None if not found."""
    stmt = (
        select(Order)
        .where(Order.order_id == order_id)
        .options(selectinload(Order.lines))
    )
    order = session.scalar(stmt)
    return _order_to_dto(order) if order is not None else None


def set_order_total(session: Session, order_id: str, total_paise: int) -> OrderDTO:
    """Update Order.total_paise via the ERP repository boundary."""
    if total_paise < 0:
        raise ERPInvalidStateError("total_paise must be non-negative")
    order = _require_order(session, order_id)
    order.total_paise = total_paise
    order.updated_at = utc_now()
    _flush(session)
    session.refresh(order)
    return _order_to_dto(order)


def apply_order_commit_effects(session: Session, order_id: str) -> OrderDTO:
    """Decrement SKU stock and increment outlet outstanding balance on committed orders."""
    order = _require_order(session, order_id)
    if order.status != OrderStatus.COMMITTED:
        raise ERPInvalidStateError(
            f"Cannot apply commit side effects for order '{order_id}' with status {order.status}"
        )

    outlet = session.get(Outlet, order.outlet_id)
    if outlet is None:
        raise ERPNotFoundError(f"Outlet '{order.outlet_id}' not found")

    for line in order.lines:
        sku = session.get(SKU, line.sku_id)
        if sku is None:
            raise ERPNotFoundError(f"SKU '{line.sku_id}' not found")
        if sku.stock_units < line.quantity:
            raise ERPInvalidStateError(
                f"Insufficient stock for SKU '{sku.sku_id}': "
                f"have {sku.stock_units}, need {line.quantity}"
            )
        sku.stock_units -= line.quantity

    outlet.outstanding_paise += order.total_paise
    order.updated_at = utc_now()
    _flush(session)
    session.refresh(order)
    return _order_to_dto(order)


def list_approvals_for_order(session: Session, order_id: str) -> list[ApprovalDTO]:
    """List approval requests for an order ordered by creation sequence."""
    stmt = (
        select(ApprovalRequest)
        .where(ApprovalRequest.order_id == order_id)
        .order_by(ApprovalRequest.request_id)
    )
    return [ApprovalDTO.model_validate(item) for item in session.scalars(stmt).all()]


def list_audit_events(
    session: Session,
    order_id: str | None = None,
) -> list[AuditEventDTO]:
    """List audit events optionally filtered by order_id in chronological order."""
    stmt = select(AuditEvent).order_by(AuditEvent.id)
    if order_id is not None:
        stmt = stmt.where(AuditEvent.order_id == order_id)
    return [AuditEventDTO.model_validate(item) for item in session.scalars(stmt).all()]


def has_valid_approval(session: Session, order_id: str) -> bool:
    """Return True if an APPROVED request exists with no subsequent line edits."""
    approved_exists = session.scalar(
        select(ApprovalRequest.request_id)
        .where(
            ApprovalRequest.order_id == order_id,
            ApprovalRequest.status == ApprovalStatus.APPROVED,
        )
        .limit(1)
    )
    if approved_exists is None:
        return False

    latest_approved_event = session.scalar(
        select(func.max(AuditEvent.id)).where(
            AuditEvent.order_id == order_id,
            AuditEvent.event_type == "APPROVAL_APPROVED",
        )
    )
    latest_edit_event = session.scalar(
        select(func.max(AuditEvent.id)).where(
            AuditEvent.order_id == order_id,
            AuditEvent.event_type == "LINES_UPDATED",
        )
    )
    if latest_edit_event is None:
        return True
    if latest_approved_event is None:
        return False
    return latest_approved_event > latest_edit_event


def has_read_back_for_current_draft(session: Session, order_id: str) -> bool:
    """Return True if READ_BACK_SPOKEN is recorded after the latest line modification."""
    latest_read_back = session.scalar(
        select(func.max(AuditEvent.id)).where(
            AuditEvent.order_id == order_id,
            AuditEvent.event_type == "READ_BACK_SPOKEN",
        )
    )
    if latest_read_back is None:
        return False
    latest_edit = session.scalar(
        select(func.max(AuditEvent.id)).where(
            AuditEvent.order_id == order_id,
            AuditEvent.event_type == "LINES_UPDATED",
        )
    )
    if latest_edit is None:
        return True
    return latest_read_back > latest_edit


def list_all_schemes(session: Session) -> list[SchemeDTO]:
    """Return all schemes ordered by scheme_id."""
    stmt = select(Scheme).order_by(Scheme.scheme_id)
    return [SchemeDTO.model_validate(item) for item in session.scalars(stmt).all()]


def get_invoice_for_order(session: Session, order_id: str) -> InvoiceDTO | None:
    """Return the invoice for a committed order, or None if not yet created."""
    invoice = session.scalar(select(Invoice).where(Invoice.order_id == order_id))
    return InvoiceDTO.model_validate(invoice) if invoice is not None else None
