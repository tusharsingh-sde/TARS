from datetime import date, datetime, timezone
from enum import StrEnum

from sqlalchemy import Date, DateTime, Enum as SQLEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OrderStatus(StrEnum):
    DRAFT = "DRAFT"
    CONFIRMING = "CONFIRMING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    COMMITTED = "COMMITTED"
    REJECTED = "REJECTED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class Base(DeclarativeBase):
    pass


class Outlet(Base):
    __tablename__ = "outlets"

    outlet_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_name: Mapped[str] = mapped_column(String(200), nullable=False)
    locality: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    route_code: Mapped[str] = mapped_column(String(16), nullable=False)
    credit_limit_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    outstanding_paise: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    aliases: Mapped[list["OutletAlias"]] = relationship(
        back_populates="outlet",
        cascade="all, delete-orphan",
    )
    orders: Mapped[list["Order"]] = relationship(back_populates="outlet")


class OutletAlias(Base):
    __tablename__ = "outlet_aliases"
    __table_args__ = (UniqueConstraint("outlet_id", "alias", name="uq_outlet_alias"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    outlet_id: Mapped[str] = mapped_column(
        ForeignKey("outlets.outlet_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    outlet: Mapped["Outlet"] = relationship(back_populates="aliases")


class SKU(Base):
    __tablename__ = "skus"

    sku_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    brand: Mapped[str] = mapped_column(String(120), nullable=False)
    product: Mapped[str] = mapped_column(String(200), nullable=False)
    pack_size: Mapped[str] = mapped_column(String(64), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    mrp_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    base_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    aliases: Mapped[list["SKUAlias"]] = relationship(
        back_populates="sku",
        cascade="all, delete-orphan",
    )
    schemes: Mapped[list["Scheme"]] = relationship(back_populates="sku")


class SKUAlias(Base):
    __tablename__ = "sku_aliases"
    __table_args__ = (UniqueConstraint("sku_id", "alias", name="uq_sku_alias"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku_id: Mapped[str] = mapped_column(
        ForeignKey("skus.sku_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    sku: Mapped["SKU"] = relationship(back_populates="aliases")


class Scheme(Base):
    __tablename__ = "schemes"

    scheme_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sku_id: Mapped[str] = mapped_column(
        ForeignKey("skus.sku_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    min_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    sku: Mapped["SKU"] = relationship(back_populates="schemes")


class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    outlet_id: Mapped[str] = mapped_column(
        ForeignKey("outlets.outlet_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rep_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        SQLEnum(OrderStatus, native_enum=False, create_constraint=True),
        nullable=False,
        default=OrderStatus.DRAFT,
    )
    total_paise: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    outlet: Mapped["Outlet"] = relationship(back_populates="orders")
    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
    )
    invoice: Mapped["Invoice | None"] = relationship(back_populates="order", uselist=False)
    approval_requests: Mapped[list["ApprovalRequest"]] = relationship(back_populates="order")
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="order")


class OrderLine(Base):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.order_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sku_id: Mapped[str] = mapped_column(
        ForeignKey("skus.sku_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    line_total_paise: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped["Order"] = relationship(back_populates="lines")
    sku: Mapped["SKU"] = relationship()


class Invoice(Base):
    __tablename__ = "invoices"

    invoice_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.order_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    total_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    order: Mapped["Order"] = relationship(back_populates="invoice")


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    request_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.order_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        SQLEnum(ApprovalStatus, native_enum=False, create_constraint=True),
        nullable=False,
        default=ApprovalStatus.PENDING,
    )
    decided_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    order: Mapped["Order"] = relationship(back_populates="approval_requests")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str | None] = mapped_column(
        ForeignKey("orders.order_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    order: Mapped["Order | None"] = relationship(back_populates="audit_events")

