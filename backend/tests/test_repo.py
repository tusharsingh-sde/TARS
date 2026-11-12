import json
import re
from collections.abc import Generator
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.erp import repo
from app.erp.errors import ERPInvalidStateError, ERPNotFoundError
from app.erp.models import (
    SKU,
    ApprovalStatus,
    AuditEvent,
    Base,
    OrderStatus,
    Outlet,
    OutletAlias,
    Scheme,
    SKUAlias,
)
from app.erp.schemas import OrderLineInput


def _enable_sqlite_fks(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest.fixture()
def seeded_session() -> Generator[Session, None, None]:
    engine: Engine = create_engine("sqlite:///:memory:", future=True)
    event.listen(engine, "connect", _enable_sqlite_fks)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        outlets = [
            Outlet(
                outlet_id="OUT-001",
                name="Sharma General Store",
                owner_name="Rameshwar Sharma",
                locality="Shastri Nagar",
                city="Meerut",
                route_code="R1",
                credit_limit_paise=2_500_000,
                outstanding_paise=2_325_000,
                aliases=[
                    OutletAlias(alias="sharma ji"),
                    OutletAlias(alias="sharma general"),
                ],
            ),
            Outlet(
                outlet_id="OUT-002",
                name="Verma Provision Mart",
                owner_name="Suresh Verma",
                locality="Sector 14",
                city="Noida",
                route_code="R1",
                credit_limit_paise=4_000_000,
                outstanding_paise=500_000,
                aliases=[
                    OutletAlias(alias="verma mart"),
                    OutletAlias(alias="verma provision"),
                ],
            ),
        ]
        skus = [
            SKU(
                sku_id="SKU-001",
                brand="Surya Crisp",
                product="Glucose Biscuit",
                pack_size="100g",
                unit="carton",
                mrp_paise=72_000,
                base_price_paise=64_000,
                stock_units=100,
                aliases=[
                    SKUAlias(alias="surya glucose"),
                    SKUAlias(alias="glucose 10 wala"),
                ],
            ),
            SKU(
                sku_id="SKU-002",
                brand="Swadista",
                product="Masala Instant Noodles",
                pack_size="70g",
                unit="carton",
                mrp_paise=134_400,
                base_price_paise=118_000,
                stock_units=80,
                aliases=[
                    SKUAlias(alias="swadista noodles"),
                    SKUAlias(alias="masala 14 wala"),
                ],
            ),
            SKU(
                sku_id="SKU-003",
                brand="Chamak Plus",
                product="Detergent Washing Powder",
                pack_size="1kg",
                unit="bag",
                mrp_paise=13_500,
                base_price_paise=11_500,
                stock_units=50,
                aliases=[
                    SKUAlias(alias="chamak 1kg"),
                    SKUAlias(alias="detergent 1kg"),
                ],
            ),
        ]
        scheme = Scheme(
            scheme_id="SCH-001",
            sku_id="SKU-001",
            min_qty=5,
            discount_bps=500,
            valid_from=date(2026, 10, 1),
            valid_to=date(2026, 12, 31),
            description="Buy 5+ cartons Surya Crisp Glucose 100g -> 5% off",
        )
        session.add_all([*outlets, *skus, scheme])
        session.commit()
        yield session


def test_lookup_outlets_skus_schemes_and_aliases(seeded_session: Session) -> None:
    assert len(repo.list_outlets(seeded_session)) == 2
    assert len(repo.list_skus(seeded_session)) == 3
    assert repo.get_outlet(seeded_session, "OUT-001") is not None
    assert repo.get_outlet(seeded_session, "OUT-999") is None
    assert repo.get_sku(seeded_session, "SKU-002") is not None
    assert repo.get_sku(seeded_session, "SKU-999") is None

    outlet_matches = repo.find_outlet_by_alias(seeded_session, "  SHARMA JI ")
    assert [o.outlet_id for o in outlet_matches] == ["OUT-001"]
    assert repo.find_outlet_by_alias(seeded_session, "sharma") == []

    sku_matches = repo.find_sku_by_alias(seeded_session, "masala 14 wala")
    assert [s.sku_id for s in sku_matches] == ["SKU-002"]

    active = repo.get_active_schemes(seeded_session, "SKU-001", date(2026, 11, 11))
    assert len(active) == 1
    assert active[0].scheme_id == "SCH-001"
    assert repo.get_active_schemes(seeded_session, "SKU-001", date(2026, 9, 15)) == []


def test_create_draft_set_lines_computes_no_totals(seeded_session: Session) -> None:
    draft = repo.create_order_draft(seeded_session, "OUT-001", "REP-01")
    assert re.fullmatch(r"ORD-\d{8}-\d{4}", draft.order_id)
    assert draft.status == OrderStatus.DRAFT
    assert draft.total_paise == 0
    assert draft.lines == []

    updated = repo.set_order_lines(
        seeded_session,
        draft.order_id,
        [
            OrderLineInput(sku_id="SKU-001", quantity=5),
            OrderLineInput(sku_id="SKU-002", quantity=2),
        ],
    )
    assert updated.status == OrderStatus.DRAFT
    assert updated.total_paise == 0
    assert len(updated.lines) == 2
    assert updated.lines[0].line_total_paise == 0

    # Replace lines clears prior lines without altering status or computing totals
    replaced = repo.set_order_lines(
        seeded_session,
        draft.order_id,
        [OrderLineInput(sku_id="SKU-003", quantity=3, unit_price_paise=11_500)],
    )
    assert replaced.status == OrderStatus.DRAFT
    assert replaced.total_paise == 0
    assert len(replaced.lines) == 1
    assert replaced.lines[0].sku_id == "SKU-003"


def test_create_invoice_refused_unless_committed(seeded_session: Session) -> None:
    draft = repo.create_order_draft(seeded_session, "OUT-002", "REP-01")
    with pytest.raises(ERPInvalidStateError):
        repo.create_invoice(seeded_session, draft.order_id)

    confirming = repo.set_order_status(seeded_session, draft.order_id, OrderStatus.CONFIRMING)
    assert confirming.status == OrderStatus.CONFIRMING
    with pytest.raises(ERPInvalidStateError):
        repo.create_invoice(seeded_session, draft.order_id)

    repo.set_order_status(seeded_session, draft.order_id, OrderStatus.COMMITTED)
    invoice = repo.create_invoice(seeded_session, draft.order_id)
    assert invoice.invoice_id == "INV-000001"
    assert invoice.order_id == draft.order_id
    assert invoice.total_paise == draft.total_paise

    with pytest.raises(ERPInvalidStateError):
        repo.create_invoice(seeded_session, draft.order_id)


def test_approval_create_and_decide(seeded_session: Session) -> None:
    draft = repo.create_order_draft(seeded_session, "OUT-001", "REP-02")
    req = repo.create_approval_request(seeded_session, draft.order_id, "CREDIT_LIMIT_EXCEEDED")
    assert req.request_id == "APR-000001"
    assert req.status == ApprovalStatus.PENDING
    assert req.decided_by is None
    assert req.decided_at is None

    decided = repo.decide_approval(seeded_session, req.request_id, ApprovalStatus.APPROVED, "MGR-01")
    assert decided.status == ApprovalStatus.APPROVED
    assert decided.decided_by == "MGR-01"
    assert decided.decided_at is not None

    with pytest.raises(ERPInvalidStateError):
        repo.decide_approval(seeded_session, req.request_id, ApprovalStatus.REJECTED, "MGR-01")


def test_append_audit_event_and_domain_errors(seeded_session: Session) -> None:
    draft = repo.create_order_draft(seeded_session, "OUT-001", "REP-01")
    repo.append_audit_event(
        seeded_session,
        draft.order_id,
        "CONFIRMATION_RECEIVED",
        {"utterance": "haan confirm karo", "channel": "voice"},
    )
    repo.append_audit_event(
        seeded_session,
        None,
        "SESSION_STARTED",
        {"rep_id": "REP-01"},
    )

    events = list(seeded_session.scalars(select(AuditEvent).order_by(AuditEvent.id)).all())
    assert len(events) == 2
    assert events[0].order_id == draft.order_id
    assert json.loads(events[0].payload_json)["utterance"] == "haan confirm karo"
    assert events[1].order_id is None

    with pytest.raises(ERPNotFoundError):
        repo.create_order_draft(seeded_session, "OUT-MISSING", "REP-01")

    with pytest.raises(ERPNotFoundError):
        repo.set_order_lines(
            seeded_session,
            draft.order_id,
            [OrderLineInput(sku_id="SKU-MISSING", quantity=1)],
        )

    with pytest.raises(ERPNotFoundError):
        repo.append_audit_event(seeded_session, "ORD-MISSING", "NOOP", {})

    with pytest.raises(ValidationError):
        OrderLineInput(sku_id="SKU-001", quantity=1, unit_price_paise=12.5)  # type: ignore[arg-type]


def test_functions_flush_without_committing(seeded_session: Session) -> None:
    seeded_session.commit()
    draft = repo.create_order_draft(seeded_session, "OUT-001", "REP-01")
    assert repo.set_order_status(seeded_session, draft.order_id, OrderStatus.CONFIRMING)
    seeded_session.rollback()

    with pytest.raises(ERPNotFoundError):
        repo.set_order_status(seeded_session, draft.order_id, OrderStatus.DRAFT)

