from typing import Annotated, Final, Literal

from google.genai import types
from pydantic import BaseModel, Field, StringConstraints

from app.erp.models import OrderStatus
from app.erp.schemas import ApprovalDTO, InvoiceDTO, OrderDTO, OutletDTO, SkuDTO
from app.rules.credit import CreditEvaluation

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

TOOL_NAMES: Final[tuple[str, ...]] = (
    "resolve_outlet",
    "add_or_update_line",
    "remove_or_correct_line",
    "check_order_rules",
    "request_manager_approval",
    "record_read_back",
    "commit_order",
    "cancel_order",
)


class OutletCandidateItem(BaseModel):
    """Serialized outlet match candidate returned to the voice model."""

    outlet_id: str
    name: str
    score: float


class SkuCandidateItem(BaseModel):
    """Serialized SKU match candidate returned to the voice model."""

    sku_id: str
    label: str
    score: float


class StockIssue(BaseModel):
    """Stock shortfall summary for one order line."""

    sku_id: str
    requested_qty: int
    available_stock: int
    shortfall: int


class DiscountIssue(BaseModel):
    """Discount policy breach summary for one order line."""

    sku_id: str
    applied_discount_bps: int
    reason: str


class ResolveOutletInput(BaseModel):
    """Input schema for resolve_outlet."""

    query: NonEmptyStr
    route_code: str | None = None


class ResolveOutletOutput(BaseModel):
    """Output schema for resolve_outlet."""

    status: Literal["MATCHED", "AMBIGUOUS", "NOT_FOUND"]
    order_id: str | None = None
    outlet: OutletDTO | None = None
    candidates: list[OutletCandidateItem] = Field(default_factory=list)
    message: str


class AddOrUpdateLineInput(BaseModel):
    """Input schema for add_or_update_line."""

    utterance_or_product: NonEmptyStr
    quantity: int | None = Field(default=None, gt=0)
    pack_size: str | None = None
    extra_discount_bps: int = Field(default=0, ge=0)


class AddOrUpdateLineOutput(BaseModel):
    """Output schema for add_or_update_line."""

    status: Literal["OK", "AMBIGUOUS", "NOT_FOUND", "NEEDS_QUANTITY", "BLOCKED", "ERROR"]
    order: OrderDTO | None = None
    sku: SkuDTO | None = None
    candidates: list[SkuCandidateItem] = Field(default_factory=list)
    needs_pack: bool = False
    upsell_hint: str | None = None
    policy_breach: str | None = None
    reason: str | None = None
    shortfall: int | None = None
    warnings: list[str] = Field(default_factory=list)
    message: str


class RemoveOrCorrectLineInput(BaseModel):
    """Input schema for remove_or_correct_line."""

    sku_id: NonEmptyStr
    new_quantity: int = Field(default=0, ge=0)


class RemoveOrCorrectLineOutput(BaseModel):
    """Output schema for remove_or_correct_line."""

    status: Literal["OK", "BLOCKED", "ERROR"]
    order: OrderDTO | None = None
    removed: bool = False
    reason: str | None = None
    shortfall: int | None = None
    message: str


class CheckOrderRulesInput(BaseModel):
    """Input schema for check_order_rules (no parameters required)."""


class CheckOrderRulesOutput(BaseModel):
    """Output schema for check_order_rules."""

    status: Literal["OK", "BLOCKED", "ERROR"]
    order_id: str | None = None
    total_paise: int = 0
    credit: CreditEvaluation | None = None
    stock_shortfalls: list[StockIssue] = Field(default_factory=list)
    discount_breaches: list[DiscountIssue] = Field(default_factory=list)
    upsell_hints: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    approved_override: bool = False
    ready_for_read_back: bool = False
    reason: str | None = None
    message: str


class RequestManagerApprovalInput(BaseModel):
    """Input schema for request_manager_approval."""

    reason_code: NonEmptyStr


class RequestManagerApprovalOutput(BaseModel):
    """Output schema for request_manager_approval."""

    status: Literal["OK", "BLOCKED", "ERROR"]
    approval: ApprovalDTO | None = None
    order_status: OrderStatus | None = None
    reason: str | None = None
    message: str


class RecordReadBackInput(BaseModel):
    """Input schema for record_read_back."""

    spoken_summary: NonEmptyStr


class RecordReadBackOutput(BaseModel):
    """Output schema for record_read_back."""

    status: Literal["OK", "BLOCKED", "ERROR"]
    order_id: str | None = None
    order_status: OrderStatus | None = None
    total_paise: int = 0
    reason: str | None = None
    excess_amount_paise: int | None = None
    message: str


class CommitOrderInput(BaseModel):
    """Input schema for commit_order."""

    confirmation_phrase: NonEmptyStr


class CommitOrderOutput(BaseModel):
    """Output schema for commit_order."""

    status: Literal["OK", "BLOCKED", "ERROR"]
    order_id: str | None = None
    order_status: OrderStatus | None = None
    invoice: InvoiceDTO | None = None
    reason: str | None = None
    message: str


class CancelOrderInput(BaseModel):
    """Input schema for cancel_order."""

    reason: NonEmptyStr


class CancelOrderOutput(BaseModel):
    """Output schema for cancel_order."""

    status: Literal["OK", "BLOCKED", "ERROR"]
    order_id: str | None = None
    order_status: OrderStatus | None = None
    reason: str | None = None
    message: str


def _build_function_declarations() -> list[types.FunctionDeclaration]:
    return [
        types.FunctionDeclaration(
            name="resolve_outlet",
            description=(
                "Match a spoken shop or customer name to a retailer outlet and open a draft order "
                "if uniquely matched."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description="Spoken outlet name, owner name, locality, or alias.",
                    ),
                    "route_code": types.Schema(
                        type=types.Type.STRING,
                        description="Optional route code (for example R1 or R2) to narrow matches.",
                        nullable=True,
                    ),
                },
                required=["query"],
            ),
        ),
        types.FunctionDeclaration(
            name="add_or_update_line",
            description=(
                "Parse a spoken line item, resolve the SKU, check stock and active schemes, and "
                "add or update the item on the active draft order."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "utterance_or_product": types.Schema(
                        type=types.Type.STRING,
                        description="Spoken line fragment or product description.",
                    ),
                    "quantity": types.Schema(
                        type=types.Type.INTEGER,
                        description="Optional explicit pack/carton quantity when known.",
                        nullable=True,
                    ),
                    "pack_size": types.Schema(
                        type=types.Type.STRING,
                        description="Optional explicit pack size (for example 1kg, 500g, 1L).",
                        nullable=True,
                    ),
                    "extra_discount_bps": types.Schema(
                        type=types.Type.INTEGER,
                        description="Extra rep-requested discount in basis points (100 bps = 1%).",
                    ),
                },
                required=["utterance_or_product"],
            ),
        ),
        types.FunctionDeclaration(
            name="remove_or_correct_line",
            description=(
                "Update an existing SKU's quantity on the active order, or remove it when "
                "new_quantity is 0."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "sku_id": types.Schema(
                        type=types.Type.STRING,
                        description="Target SKU ID (for example SKU-001).",
                    ),
                    "new_quantity": types.Schema(
                        type=types.Type.INTEGER,
                        description="New positive quantity, or 0 to remove the line item.",
                    ),
                },
                required=["sku_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="check_order_rules",
            description=(
                "Evaluate credit limit, stock availability, and discount policy rules for the "
                "active draft order."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={},
            ),
        ),
        types.FunctionDeclaration(
            name="request_manager_approval",
            description=(
                "Submit the active draft order to the sales manager approval queue when a credit "
                "or discount rule breach blocks direct confirmation."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "reason_code": types.Schema(
                        type=types.Type.STRING,
                        description="Rule breach reason code, e.g. CREDIT_LIMIT_EXCEEDED.",
                    ),
                },
                required=["reason_code"],
            ),
        ),
        types.FunctionDeclaration(
            name="record_read_back",
            description=(
                "Record the complete spoken read-back summary after all rules pass or manager "
                "approval is granted, moving the order to CONFIRMING."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "spoken_summary": types.Schema(
                        type=types.Type.STRING,
                        description="Exact summary of outlet, items, discounts, and Rupee total read back to the rep.",
                    ),
                },
                required=["spoken_summary"],
            ),
        ),
        types.FunctionDeclaration(
            name="commit_order",
            description=(
                "Commit the confirming order to the ERP and generate an invoice after the rep "
                "explicitly confirms the read-back."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "confirmation_phrase": types.Schema(
                        type=types.Type.STRING,
                        description="Exact confirmation phrase spoken by the rep (e.g. 'Haan confirm').",
                    ),
                },
                required=["confirmation_phrase"],
            ),
        ),
        types.FunctionDeclaration(
            name="cancel_order",
            description="Cancel the active uncommitted draft or confirming order.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "reason": types.Schema(
                        type=types.Type.STRING,
                        description="Reason the order was cancelled by the rep.",
                    ),
                },
                required=["reason"],
            ),
        ),
    ]


def get_gemini_tools() -> list[types.Tool]:
    """Return Gemini Live Tool declarations for all supported order-capture tools."""
    return [types.Tool(function_declarations=_build_function_declarations())]

