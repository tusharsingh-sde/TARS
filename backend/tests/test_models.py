from datetime import date
from typing import Any

import pytest
from sqlalchemy import Float, Numeric, create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.erp.models import (
    ApprovalRequest,
    ApprovalStatus,
    AuditEvent,
    Base,
    Invoice,
    Order,
    OrderLine,
    OrderStatus,
    Outlet,
    OutletAlias,
    SKU,
    SKUAlias,
    Scheme,
)


def _enable_sqlite_fks(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest.fixture()
def engine() -> Engine:
    eng = create_engine("sqlite:///:memory:", future=True)
    event.listen(eng, "connect", _enable_sqlite_fks)
    Base.metadata.create_all(eng)
    return eng


def test_tables_created_and_no_float_or_numeric_money(engine: Engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    expected = {
        "outlets",
        "outlet_aliases",
        "skus",
        "sku_aliases",
        "schemes",
        "orders",
        "order_lines",
        "invoices",
        "approval_requests",
        "audit_events",
    }
    assert expected.issubset(table_names)

    for table in Base.metadata.tables.values():
        for col in table.columns:
            assert not isinstance(col.type, (Float, Numeric)), (
                f"Disallowed Float/Numeric column {table.name}.{col.name}"
            )


def test_insert_records_and_foreign_keys(engine: Engine) -> None:
    with Session(engine) as session:
        outlet = Outlet(
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
        )
        sku = SKU(
            sku_id="SKU-001",
            brand="Surya Crisp",
            product="Glucose Biscuit",
            pack_size="100g",
            unit="carton",
            mrp_paise=72_000,
            base_price_paise=64_000,
            stock_units=140,
            aliases=[
                SKUAlias(alias="surya glucose"),
                SKUAlias(alias="glucose 10 wala"),
            ],
        )
        scheme = Scheme(
            scheme_id="SCH-001",
            sku_id="SKU-001",
            min_qty=5,
            discount_bps=500,
            valid_from=date(2026, 10, 1),
            valid_to=date(2026, 12, 31),
            description="Buy 5+ cartons Surya Crisp Glucose 100g -> 5% off",
        )
        order = Order(
            order_id="ORD-20261111-0001",
            outlet_id="OUT-001",
            rep_id="REP-01",
            status=OrderStatus.DRAFT,
            total_paise=304_000,
            lines=[
                OrderLine(
                    sku_id="SKU-001",
                    quantity=5,
                    unit_price_paise=64_000,
                    discount_bps=500,
                    line_total_paise=304_000,
                )
            ],
        )
        session.add_all([outlet, sku, scheme, order])
        session.commit()

        saved_order = session.get(Order, "ORD-20261111-0001")
        assert saved_order is not None
        assert saved_order.outlet.name == "Sharma General Store"
        assert len(saved_order.outlet.aliases) == 2
        assert len(saved_order.lines) == 1
        assert saved_order.lines[0].sku.sku_id == "SKU-001"
        assert len(saved_order.lines[0].sku.aliases) == 2

        approval = ApprovalRequest(
            request_id="REQ-0001",
            order_id=saved_order.order_id,
            reason_code="CREDIT_LIMIT_EXCEEDED",
            status=ApprovalStatus.PENDING,
        )
        audit = AuditEvent(
            order_id=saved_order.order_id,
            event_type="ORDER_CREATED",
            payload_json='{"status": "DRAFT"}',
        )
        session.add_all([approval, audit])
        session.commit()


def test_foreign_key_enforcement_and_invoice_unique_constraint(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(
            Outlet(
                outlet_id="OUT-001",
                name="Sharma General Store",
                owner_name="Rameshwar Sharma",
                locality="Shastri Nagar",
                city="Meerut",
                route_code="R1",
                credit_limit_paise=2_500_000,
                outstanding_paise=0,
            )
        )
        session.add(
            Order(
                order_id="ORD-20261111-0001",
                outlet_id="OUT-001",
                rep_id="REP-01",
                status=OrderStatus.COMMITTED,
                total_paise=64_000,
            )
        )
        session.commit()

        # Invalid FK on order_lines.sku_id must fail
        session.add(
            OrderLine(
                order_id="ORD-20261111-0001",
                sku_id="SKU-MISSING",
                quantity=1,
                unit_price_paise=64_000,
                discount_bps=0,
                line_total_paise=64_000,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        # Invalid FK on orders.outlet_id must fail
        session.add(
            Order(
                order_id="ORD-20261111-0002",
                outlet_id="OUT-MISSING",
                rep_id="REP-01",
                status=OrderStatus.DRAFT,
                total_paise=0,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        # First invoice succeeds
        session.add(
            Invoice(
                invoice_id="INV-000001",
                order_id="ORD-20261111-0001",
                total_paise=64_000,
            )
        )
        session.commit()

        # Second invoice for the same order_id violates unique constraint
        session.add(
            Invoice(
                invoice_id="INV-000002",
                order_id="ORD-20261111-0001",
                total_paise=64_000,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

