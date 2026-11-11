from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.erp.models import ApprovalStatus, OrderStatus

Paise = Annotated[int, StrictInt()]


class OutletDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    outlet_id: str
    name: str
    owner_name: str
    locality: str
    city: str
    route_code: str
    credit_limit_paise: Paise
    outstanding_paise: Paise
    aliases: list[str]


class SkuDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sku_id: str
    brand: str
    product: str
    pack_size: str
    unit: str
    mrp_paise: Paise
    base_price_paise: Paise
    stock_units: int
    aliases: list[str]


class SchemeDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scheme_id: str
    sku_id: str
    min_qty: int
    discount_bps: int
    valid_from: date
    valid_to: date
    description: str


class OrderLineInput(BaseModel):
    sku_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    unit_price_paise: Paise = Field(default=0, ge=0)
    discount_bps: int = Field(default=0, ge=0)
    line_total_paise: Paise = Field(default=0, ge=0)


class OrderLineDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: str
    sku_id: str
    quantity: int
    unit_price_paise: Paise
    discount_bps: int
    line_total_paise: Paise


class OrderDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    outlet_id: str
    rep_id: str
    status: OrderStatus
    total_paise: Paise
    lines: list[OrderLineDTO]
    created_at: datetime
    updated_at: datetime


class InvoiceDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    invoice_id: str
    order_id: str
    total_paise: Paise
    created_at: datetime


class ApprovalDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_id: str
    order_id: str
    reason_code: str
    status: ApprovalStatus
    decided_by: str | None
    decided_at: datetime | None
    created_at: datetime


class AuditEventDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: str | None
    event_type: str
    payload_json: str
    created_at: datetime

