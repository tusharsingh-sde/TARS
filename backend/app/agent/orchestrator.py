import time
from collections.abc import Callable
from datetime import date
from typing import Final, Literal, cast

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agent.state import InvalidOrderTransitionError, transition_order
from app.agent.tools import (
    AddOrUpdateLineInput,
    AddOrUpdateLineOutput,
    CancelOrderInput,
    CancelOrderOutput,
    CheckOrderRulesInput,
    CheckOrderRulesOutput,
    CommitOrderInput,
    CommitOrderOutput,
    DiscountIssue,
    OutletCandidateItem,
    RecordReadBackInput,
    RecordReadBackOutput,
    RemoveOrCorrectLineInput,
    RemoveOrCorrectLineOutput,
    RequestManagerApprovalInput,
    RequestManagerApprovalOutput,
    ResolveOutletInput,
    ResolveOutletOutput,
    SkuCandidateItem,
    StockIssue,
)
from app.approvals.service import submit_approval_request
from app.erp import repo
from app.erp.errors import ERPError, ERPInvalidStateError
from app.erp.models import OrderStatus
from app.erp.schemas import OrderDTO, OrderLineInput, OutletDTO, SkuDTO
from app.matching.line_parser import (
    MAX_QUANTITY_EXCLUSIVE,
    MIN_QUANTITY,
    ParsedLine,
    parse_line,
)
from app.matching.outlets import resolve_outlet as match_outlet
from app.matching.skus import MULTIPLE_PACK_SIZES_WARNING, resolve_sku
from app.rules.credit import evaluate_credit
from app.rules.discount import DiscountEvaluation, evaluate_discount
from app.rules.stock import evaluate_stock
from app.trace.logger import record_tool_trace
from app.trace.stream import TraceStatus, TraceStreamHub

BPS_DENOMINATOR: Final[int] = 10000
BLOCKED_TOOL_STATUSES: Final[frozenset[str]] = frozenset({"BLOCKED"})
ERROR_TOOL_STATUSES: Final[frozenset[str]] = frozenset({"ERROR"})
EDITABLE_STATUSES: Final[frozenset[OrderStatus]] = frozenset(
    {OrderStatus.DRAFT, OrderStatus.CONFIRMING}
)


class _RuleSnapshot:
    """Internal snapshot of rule checks across all lines of an order."""

    def __init__(
        self,
        *,
        order: OrderDTO,
        outlet: OutletDTO,
        stock_shortfalls: list[StockIssue],
        discount_breaches: list[DiscountIssue],
        upsell_hints: list[str],
        excess_amount_paise: int,
        credit_approved: bool,
        approved_override: bool,
    ) -> None:
        self.order = order
        self.outlet = outlet
        self.stock_shortfalls = stock_shortfalls
        self.discount_breaches = discount_breaches
        self.upsell_hints = upsell_hints
        self.excess_amount_paise = excess_amount_paise
        self.credit_approved = credit_approved
        self.approved_override = approved_override

    @property
    def requires_approval(self) -> bool:
        return (not self.credit_approved) or bool(self.discount_breaches)

    @property
    def unresolved_approval(self) -> bool:
        return self.requires_approval and not self.approved_override


class OrderOrchestrator:
    """Stateful session orchestrator enforcing rules and state transitions across tools."""

    def __init__(
        self,
        db: Session,
        *,
        session_id: str,
        rep_id: str,
        as_of: date | None = None,
        trace_hub: TraceStreamHub | None = None,
        auto_commit: bool = True,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        if not rep_id.strip():
            raise ValueError("rep_id must not be empty")

        self._db = db
        self.session_id = session_id.strip()
        self.rep_id = rep_id.strip()
        self.as_of: date = as_of if as_of is not None else date.today()
        self.trace_hub = trace_hub if trace_hub is not None else TraceStreamHub()
        self._auto_commit = auto_commit

        self.active_order_id: str | None = None
        self.active_outlet_id: str | None = None
        self.read_back_spoken: bool = False
        self.extra_discount_by_sku: dict[str, int] = {}

        self._handlers: dict[str, Callable[[dict[str, object]], dict[str, object]]] = {
            "resolve_outlet": self._handle_resolve_outlet,
            "add_or_update_line": self._handle_add_or_update_line,
            "remove_or_correct_line": self._handle_remove_or_correct_line,
            "check_order_rules": self._handle_check_order_rules,
            "request_manager_approval": self._handle_request_manager_approval,
            "record_read_back": self._handle_record_read_back,
            "commit_order": self._handle_commit_order,
            "cancel_order": self._handle_cancel_order,
        }

    def dispatch_tool(self, name: str, args: dict[str, object]) -> dict[str, object]:
        """Validate, execute, audit, and trace one tool call without raising to the voice loop."""
        start_ns = time.perf_counter_ns()
        handler = self._handlers.get(name)
        if handler is None:
            output: dict[str, object] = {
                "status": "ERROR",
                "reason": "UNKNOWN_TOOL",
                "message": f"Unknown tool '{name}'",
            }
            return self._finalize_trace(name, args, output, "ERROR", start_ns)

        prev_order_id = self.active_order_id
        prev_outlet_id = self.active_outlet_id
        prev_read_back = self.read_back_spoken
        prev_discounts = dict(self.extra_discount_by_sku)

        try:
            with self._db.begin_nested():
                output = handler(args)
        except ValidationError as exc:
            self._restore_state(prev_order_id, prev_outlet_id, prev_read_back, prev_discounts)
            output = {
                "status": "ERROR",
                "reason": "INVALID_TOOL_ARGS",
                "message": f"Invalid arguments for '{name}': {exc.errors()[0]['msg']}",
            }
        except (InvalidOrderTransitionError, ERPInvalidStateError) as exc:
            self._restore_state(prev_order_id, prev_outlet_id, prev_read_back, prev_discounts)
            output = {
                "status": "BLOCKED",
                "reason": "INVALID_STATE",
                "message": str(exc),
            }
        except (ERPError, ValueError) as exc:
            self._restore_state(prev_order_id, prev_outlet_id, prev_read_back, prev_discounts)
            output = {
                "status": "ERROR",
                "reason": "DOMAIN_ERROR",
                "message": str(exc),
            }

        trace_status = self._map_trace_status(output.get("status"))
        return self._finalize_trace(name, args, output, trace_status, start_ns)

    def _finalize_trace(
        self,
        tool_name: str,
        args: dict[str, object],
        output: dict[str, object],
        trace_status: TraceStatus,
        start_ns: int,
    ) -> dict[str, object]:
        latency_ms = max(0, (time.perf_counter_ns() - start_ns) // 1_000_000)
        trace_order_id = self.active_order_id if self._order_exists(self.active_order_id) else None
        record_tool_trace(
            self._db,
            self.trace_hub,
            session_id=self.session_id,
            order_id=trace_order_id,
            tool_name=tool_name,
            input_payload=args,
            output_payload=output,
            status=trace_status,
            latency_ms=latency_ms,
        )
        if self._auto_commit:
            self._db.commit()
        else:
            self._db.flush()
        return output

    def _restore_state(
        self,
        order_id: str | None,
        outlet_id: str | None,
        read_back_spoken: bool,
        extra_discounts: dict[str, int],
    ) -> None:
        self.active_order_id = order_id
        self.active_outlet_id = outlet_id
        self.read_back_spoken = read_back_spoken
        self.extra_discount_by_sku = extra_discounts

    def _order_exists(self, order_id: str | None) -> bool:
        if order_id is None:
            return False
        return repo.get_order(self._db, order_id) is not None

    @staticmethod
    def _map_trace_status(raw_status: object) -> TraceStatus:
        if raw_status in BLOCKED_TOOL_STATUSES:
            return "BLOCKED"
        if raw_status in ERROR_TOOL_STATUSES:
            return "ERROR"
        return "OK"

    def _require_active_order(self) -> OrderDTO | None:
        if self.active_order_id is None:
            return None
        return repo.get_order(self._db, self.active_order_id)

    def _handle_resolve_outlet(self, args: dict[str, object]) -> dict[str, object]:
        payload = ResolveOutletInput.model_validate(args)
        outlets = repo.list_outlets(self._db)
        if payload.route_code and payload.route_code.strip():
            target_route = payload.route_code.strip().lower()
            outlets = [o for o in outlets if o.route_code.lower() == target_route]

        match = match_outlet(payload.query, outlets)
        candidates = [
            OutletCandidateItem(outlet_id=c.outlet_id, name=c.name, score=c.score)
            for c in match.candidates
        ]

        if match.status != "MATCHED" or match.best is None:
            message = (
                "Multiple matching outlets found; ask the rep to clarify shop name or locality."
                if match.status == "AMBIGUOUS"
                else "No matching outlet found; ask the rep to repeat the shop name."
            )
            return cast(
                dict[str, object],
                ResolveOutletOutput(
                    status=match.status,
                    candidates=candidates,
                    message=message,
                ).model_dump(mode="json"),
            )

        outlet = repo.get_outlet(self._db, match.best.outlet_id)
        if outlet is None:
            return cast(
                dict[str, object],
                ResolveOutletOutput(
                    status="NOT_FOUND",
                    candidates=[],
                    message="Resolved outlet not found in database.",
                ).model_dump(mode="json"),
            )

        self.active_outlet_id = outlet.outlet_id
        order = self._ensure_draft_for_outlet(outlet.outlet_id)
        return cast(
            dict[str, object],
            ResolveOutletOutput(
                status="MATCHED",
                order_id=order.order_id,
                outlet=outlet,
                candidates=candidates,
                message=f"Outlet matched to {outlet.name}; draft order {order.order_id} ready.",
            ).model_dump(mode="json"),
        )

    def _ensure_draft_for_outlet(self, outlet_id: str) -> OrderDTO:
        existing = self._require_active_order()
        if (
            existing is not None
            and existing.outlet_id == outlet_id
            and existing.status in EDITABLE_STATUSES
        ):
            return existing

        order = repo.create_order_draft(self._db, outlet_id=outlet_id, rep_id=self.rep_id)
        self.active_order_id = order.order_id
        self.read_back_spoken = False
        self.extra_discount_by_sku = {}
        return order

    def _prepare_parsed_line(self, payload: AddOrUpdateLineInput) -> ParsedLine:
        parsed = parse_line(payload.utterance_or_product)
        warnings = list(parsed.warnings)
        pack_size = parsed.pack_size
        quantity = parsed.quantity

        if payload.pack_size is not None and payload.pack_size.strip():
            pack_size = "".join(payload.pack_size.split()).lower()
            warnings = [w for w in warnings if w != MULTIPLE_PACK_SIZES_WARNING]

        if payload.quantity is not None:
            quantity = payload.quantity
            warnings = [
                w
                for w in warnings
                if w not in {"no quantity", "multiple quantities"}
                and not w.startswith("quantity out of range:")
            ]

        return parsed.model_copy(
            update={"pack_size": pack_size, "quantity": quantity, "warnings": warnings}
        )

    @staticmethod
    def _compute_line_total_paise(
        *,
        quantity: int,
        unit_price_paise: int,
        discount_bps: int,
    ) -> int:
        if discount_bps < 0 or discount_bps > BPS_DENOMINATOR:
            raise ValueError(f"applied discount_bps out of range 0..{BPS_DENOMINATOR}: {discount_bps}")
        return (quantity * unit_price_paise * (BPS_DENOMINATOR - discount_bps)) // BPS_DENOMINATOR

    def _reset_confirming_to_draft(self, order: OrderDTO) -> OrderDTO:
        if order.status == OrderStatus.CONFIRMING:
            next_status = transition_order(order.status, OrderStatus.DRAFT)
            order = repo.set_order_status(self._db, order.order_id, next_status)
        self.read_back_spoken = False
        return order

    def _handle_add_or_update_line(self, args: dict[str, object]) -> dict[str, object]:
        payload = AddOrUpdateLineInput.model_validate(args)
        order = self._require_active_order()
        if order is None:
            return cast(
                dict[str, object],
                AddOrUpdateLineOutput(
                    status="BLOCKED",
                    reason="NO_ACTIVE_ORDER",
                    message="Resolve the outlet before adding order items.",
                ).model_dump(mode="json"),
            )
        if order.status not in EDITABLE_STATUSES:
            return cast(
                dict[str, object],
                AddOrUpdateLineOutput(
                    status="BLOCKED",
                    reason="ORDER_NOT_EDITABLE",
                    message=f"Cannot modify order in status {order.status.value}.",
                ).model_dump(mode="json"),
            )

        parsed = self._prepare_parsed_line(payload)
        sku_match = resolve_sku(parsed, repo.list_skus(self._db))
        candidates = [
            SkuCandidateItem(sku_id=c.sku_id, label=c.label, score=c.score)
            for c in sku_match.candidates
        ]

        if sku_match.status != "MATCHED" or sku_match.best is None:
            unresolved_status: Literal["AMBIGUOUS", "NOT_FOUND"] = (
                "AMBIGUOUS" if sku_match.status == "AMBIGUOUS" else "NOT_FOUND"
            )
            message = (
                "Multiple matching SKUs found; ask the rep to clarify product or pack size."
                if unresolved_status == "AMBIGUOUS"
                else "No matching SKU found; ask the rep to repeat the product name and pack size."
            )
            return cast(
                dict[str, object],
                AddOrUpdateLineOutput(
                    status=unresolved_status,
                    candidates=candidates,
                    needs_pack=sku_match.needs_pack,
                    warnings=parsed.warnings,
                    message=message,
                ).model_dump(mode="json"),
            )

        sku = repo.get_sku(self._db, sku_match.best.sku_id)
        if sku is None:
            return cast(
                dict[str, object],
                AddOrUpdateLineOutput(
                    status="NOT_FOUND",
                    message="Matched SKU record missing from database.",
                ).model_dump(mode="json"),
            )

        qty = parsed.quantity
        if qty is None or qty < MIN_QUANTITY or qty >= MAX_QUANTITY_EXCLUSIVE:
            return cast(
                dict[str, object],
                AddOrUpdateLineOutput(
                    status="NEEDS_QUANTITY",
                    sku=sku,
                    warnings=parsed.warnings,
                    reason="INVALID_OR_MISSING_QUANTITY",
                    message="Quantity is missing or out of valid range (1-499); ask the rep.",
                ).model_dump(mode="json"),
            )

        return self._apply_sku_line_to_order(
            order=order,
            sku=sku,
            qty=qty,
            extra_discount_bps=payload.extra_discount_bps,
            warnings=parsed.warnings,
        )

    def _apply_sku_line_to_order(
        self,
        *,
        order: OrderDTO,
        sku: SkuDTO,
        qty: int,
        extra_discount_bps: int,
        warnings: list[str],
    ) -> dict[str, object]:
        stock_eval = evaluate_stock(sku, requested_packs=qty)
        if not stock_eval.is_available:
            return cast(
                dict[str, object],
                AddOrUpdateLineOutput(
                    status="BLOCKED",
                    sku=sku,
                    reason=stock_eval.reason,
                    shortfall=stock_eval.shortfall,
                    warnings=warnings,
                    message=(
                        f"Insufficient stock for {sku.brand} {sku.product} ({sku.pack_size}): "
                        f"only {sku.stock_units} available."
                    ),
                ).model_dump(mode="json"),
            )

        schemes = repo.get_active_schemes(self._db, sku.sku_id, self.as_of)
        discount_eval: DiscountEvaluation = evaluate_discount(
            sku,
            qty,
            schemes,
            as_of=self.as_of,
            extra_discount_bps=extra_discount_bps,
        )
        line_total_paise = self._compute_line_total_paise(
            quantity=qty,
            unit_price_paise=sku.base_price_paise,
            discount_bps=discount_eval.applied_discount_bps,
        )

        order = self._reset_confirming_to_draft(order)
        self.extra_discount_by_sku[sku.sku_id] = extra_discount_bps

        new_line = OrderLineInput(
            sku_id=sku.sku_id,
            quantity=qty,
            unit_price_paise=sku.base_price_paise,
            discount_bps=discount_eval.applied_discount_bps,
            line_total_paise=line_total_paise,
        )
        updated_inputs: list[OrderLineInput] = []
        replaced = False
        for existing_line in order.lines:
            if existing_line.sku_id == sku.sku_id:
                updated_inputs.append(new_line)
                replaced = True
            else:
                updated_inputs.append(
                    OrderLineInput(
                        sku_id=existing_line.sku_id,
                        quantity=existing_line.quantity,
                        unit_price_paise=existing_line.unit_price_paise,
                        discount_bps=existing_line.discount_bps,
                        line_total_paise=existing_line.line_total_paise,
                    )
                )
        if not replaced:
            updated_inputs.append(new_line)

        updated_order = repo.set_order_lines(self._db, order.order_id, updated_inputs)
        grand_total = sum(line.line_total_paise for line in updated_order.lines)
        updated_order = repo.set_order_total(self._db, order.order_id, grand_total)
        repo.append_audit_event(
            self._db,
            updated_order.order_id,
            event_type="LINES_UPDATED",
            payload={"sku_id": sku.sku_id, "quantity": qty, "total_paise": grand_total},
        )

        return cast(
            dict[str, object],
            AddOrUpdateLineOutput(
                status="OK",
                order=updated_order,
                sku=sku,
                upsell_hint=discount_eval.upsell_hint,
                policy_breach=discount_eval.policy_breach,
                warnings=warnings,
                message=f"Updated {qty} {sku.unit} of {sku.brand} {sku.product} ({sku.pack_size}).",
            ).model_dump(mode="json"),
        )

    def _handle_remove_or_correct_line(self, args: dict[str, object]) -> dict[str, object]:
        payload = RemoveOrCorrectLineInput.model_validate(args)
        order = self._require_active_order()
        if order is None:
            return cast(
                dict[str, object],
                RemoveOrCorrectLineOutput(
                    status="BLOCKED",
                    reason="NO_ACTIVE_ORDER",
                    message="No active draft order to modify.",
                ).model_dump(mode="json"),
            )
        if order.status not in EDITABLE_STATUSES:
            return cast(
                dict[str, object],
                RemoveOrCorrectLineOutput(
                    status="BLOCKED",
                    reason="ORDER_NOT_EDITABLE",
                    message=f"Cannot modify order in status {order.status.value}.",
                ).model_dump(mode="json"),
            )

        target_line = next((line for line in order.lines if line.sku_id == payload.sku_id), None)
        if target_line is None:
            return cast(
                dict[str, object],
                RemoveOrCorrectLineOutput(
                    status="ERROR",
                    reason="LINE_NOT_FOUND",
                    message=f"SKU '{payload.sku_id}' is not in order '{order.order_id}'.",
                ).model_dump(mode="json"),
            )

        new_inputs: list[OrderLineInput] = []
        if payload.new_quantity == 0:
            self.extra_discount_by_sku.pop(payload.sku_id, None)
            new_inputs = [
                OrderLineInput(
                    sku_id=line.sku_id,
                    quantity=line.quantity,
                    unit_price_paise=line.unit_price_paise,
                    discount_bps=line.discount_bps,
                    line_total_paise=line.line_total_paise,
                )
                for line in order.lines
                if line.sku_id != payload.sku_id
            ]
        else:
            if payload.new_quantity >= MAX_QUANTITY_EXCLUSIVE:
                return cast(
                    dict[str, object],
                    RemoveOrCorrectLineOutput(
                        status="ERROR",
                        reason="INVALID_QUANTITY",
                        message="Quantity must be under 500.",
                    ).model_dump(mode="json"),
                )
            sku = repo.get_sku(self._db, payload.sku_id)
            if sku is None:
                return cast(
                    dict[str, object],
                    RemoveOrCorrectLineOutput(
                        status="ERROR",
                        reason="SKU_NOT_FOUND",
                        message=f"SKU '{payload.sku_id}' not found.",
                    ).model_dump(mode="json"),
                )
            stock_eval = evaluate_stock(sku, requested_packs=payload.new_quantity)
            if not stock_eval.is_available:
                return cast(
                    dict[str, object],
                    RemoveOrCorrectLineOutput(
                        status="BLOCKED",
                        reason=stock_eval.reason,
                        shortfall=stock_eval.shortfall,
                        message=(
                            f"Insufficient stock for {sku.brand} {sku.product}: "
                            f"only {sku.stock_units} available."
                        ),
                    ).model_dump(mode="json"),
                )
            extra_bps = self.extra_discount_by_sku.get(payload.sku_id, 0)
            discount_eval = evaluate_discount(
                sku,
                payload.new_quantity,
                repo.get_active_schemes(self._db, sku.sku_id, self.as_of),
                as_of=self.as_of,
                extra_discount_bps=extra_bps,
            )
            line_total = self._compute_line_total_paise(
                quantity=payload.new_quantity,
                unit_price_paise=sku.base_price_paise,
                discount_bps=discount_eval.applied_discount_bps,
            )
            for line in order.lines:
                if line.sku_id == payload.sku_id:
                    new_inputs.append(
                        OrderLineInput(
                            sku_id=sku.sku_id,
                            quantity=payload.new_quantity,
                            unit_price_paise=sku.base_price_paise,
                            discount_bps=discount_eval.applied_discount_bps,
                            line_total_paise=line_total,
                        )
                    )
                else:
                    new_inputs.append(
                        OrderLineInput(
                            sku_id=line.sku_id,
                            quantity=line.quantity,
                            unit_price_paise=line.unit_price_paise,
                            discount_bps=line.discount_bps,
                            line_total_paise=line.line_total_paise,
                        )
                    )

        order = self._reset_confirming_to_draft(order)
        updated_order = repo.set_order_lines(self._db, order.order_id, new_inputs)
        grand_total = sum(line.line_total_paise for line in updated_order.lines)
        updated_order = repo.set_order_total(self._db, order.order_id, grand_total)
        repo.append_audit_event(
            self._db,
            updated_order.order_id,
            event_type="LINES_UPDATED",
            payload={
                "sku_id": payload.sku_id,
                "quantity": payload.new_quantity,
                "total_paise": grand_total,
            },
        )

        return cast(
            dict[str, object],
            RemoveOrCorrectLineOutput(
                status="OK",
                order=updated_order,
                removed=payload.new_quantity == 0,
                message=(
                    f"Removed SKU {payload.sku_id} from order."
                    if payload.new_quantity == 0
                    else f"Updated SKU {payload.sku_id} quantity to {payload.new_quantity}."
                ),
            ).model_dump(mode="json"),
        )

    def _evaluate_order_snapshot(self, order: OrderDTO) -> _RuleSnapshot:
        outlet = repo.get_outlet(self._db, order.outlet_id)
        if outlet is None:
            raise ValueError(f"Outlet '{order.outlet_id}' not found")

        credit_eval = evaluate_credit(outlet, order.total_paise)
        stock_shortfalls: list[StockIssue] = []
        discount_breaches: list[DiscountIssue] = []
        upsell_hints: list[str] = []

        for line in order.lines:
            sku = repo.get_sku(self._db, line.sku_id)
            if sku is None:
                raise ValueError(f"SKU '{line.sku_id}' not found")
            stock_eval = evaluate_stock(sku, requested_packs=line.quantity)
            if not stock_eval.is_available:
                stock_shortfalls.append(
                    StockIssue(
                        sku_id=sku.sku_id,
                        requested_qty=line.quantity,
                        available_stock=sku.stock_units,
                        shortfall=stock_eval.shortfall,
                    )
                )
            extra_bps = self.extra_discount_by_sku.get(line.sku_id, 0)
            discount_eval = evaluate_discount(
                sku,
                line.quantity,
                repo.get_active_schemes(self._db, sku.sku_id, self.as_of),
                as_of=self.as_of,
                extra_discount_bps=extra_bps,
            )
            if discount_eval.policy_breach is not None:
                discount_breaches.append(
                    DiscountIssue(
                        sku_id=sku.sku_id,
                        applied_discount_bps=discount_eval.applied_discount_bps,
                        reason=discount_eval.policy_breach,
                    )
                )
            if discount_eval.upsell_hint is not None:
                upsell_hints.append(discount_eval.upsell_hint)

        approved_override = repo.has_valid_approval(self._db, order.order_id)
        return _RuleSnapshot(
            order=order,
            outlet=outlet,
            stock_shortfalls=stock_shortfalls,
            discount_breaches=discount_breaches,
            upsell_hints=upsell_hints,
            excess_amount_paise=credit_eval.excess_amount_paise,
            credit_approved=credit_eval.is_approved,
            approved_override=approved_override,
        )

    def _handle_check_order_rules(self, args: dict[str, object]) -> dict[str, object]:
        CheckOrderRulesInput.model_validate(args)
        order = self._require_active_order()
        if order is None:
            return cast(
                dict[str, object],
                CheckOrderRulesOutput(
                    status="BLOCKED",
                    reason="NO_ACTIVE_ORDER",
                    message="No active order to check.",
                ).model_dump(mode="json"),
            )

        snapshot = self._evaluate_order_snapshot(order)
        credit_eval = evaluate_credit(snapshot.outlet, order.total_paise)
        ready = (
            bool(order.lines)
            and not snapshot.stock_shortfalls
            and not snapshot.unresolved_approval
        )
        return cast(
            dict[str, object],
            CheckOrderRulesOutput(
                status="OK",
                order_id=order.order_id,
                total_paise=order.total_paise,
                credit=credit_eval,
                stock_shortfalls=snapshot.stock_shortfalls,
                discount_breaches=snapshot.discount_breaches,
                upsell_hints=snapshot.upsell_hints,
                requires_approval=snapshot.requires_approval,
                approved_override=snapshot.approved_override,
                ready_for_read_back=ready,
                message=(
                    "Order passes all rules and is ready for read-back."
                    if ready
                    else "Order has rule breaches, stock shortfalls, or no items."
                ),
            ).model_dump(mode="json"),
        )

    def _handle_request_manager_approval(self, args: dict[str, object]) -> dict[str, object]:
        payload = RequestManagerApprovalInput.model_validate(args)
        order = self._require_active_order()
        if order is None:
            return cast(
                dict[str, object],
                RequestManagerApprovalOutput(
                    status="BLOCKED",
                    reason="NO_ACTIVE_ORDER",
                    message="No active order to submit for approval.",
                ).model_dump(mode="json"),
            )
        if not order.lines:
            return cast(
                dict[str, object],
                RequestManagerApprovalOutput(
                    status="BLOCKED",
                    reason="EMPTY_ORDER",
                    message="Cannot request approval for an empty order.",
                ).model_dump(mode="json"),
            )

        snapshot = self._evaluate_order_snapshot(order)
        if not snapshot.requires_approval:
            return cast(
                dict[str, object],
                RequestManagerApprovalOutput(
                    status="BLOCKED",
                    reason="NO_APPROVAL_REQUIRED",
                    message="Order has no credit or discount rule breaches requiring approval.",
                ).model_dump(mode="json"),
            )

        approval = submit_approval_request(self._db, order.order_id, payload.reason_code)
        self.read_back_spoken = False
        return cast(
            dict[str, object],
            RequestManagerApprovalOutput(
                status="OK",
                approval=approval,
                order_status=OrderStatus.PENDING_APPROVAL,
                message=f"Submitted approval request {approval.request_id} for {order.order_id}.",
            ).model_dump(mode="json"),
        )

    def _handle_record_read_back(self, args: dict[str, object]) -> dict[str, object]:
        payload = RecordReadBackInput.model_validate(args)
        order = self._require_active_order()
        if order is None:
            return cast(
                dict[str, object],
                RecordReadBackOutput(
                    status="BLOCKED",
                    reason="NO_ACTIVE_ORDER",
                    message="No active order to read back.",
                ).model_dump(mode="json"),
            )
        if not order.lines:
            return cast(
                dict[str, object],
                RecordReadBackOutput(
                    status="BLOCKED",
                    order_id=order.order_id,
                    order_status=order.status,
                    reason="EMPTY_ORDER",
                    message="Cannot read back an empty order.",
                ).model_dump(mode="json"),
            )
        if order.status == OrderStatus.PENDING_APPROVAL:
            return cast(
                dict[str, object],
                RecordReadBackOutput(
                    status="BLOCKED",
                    order_id=order.order_id,
                    order_status=order.status,
                    reason="PENDING_MANAGER_APPROVAL",
                    message="Order is waiting for manager approval before read-back.",
                ).model_dump(mode="json"),
            )

        snapshot = self._evaluate_order_snapshot(order)
        if snapshot.stock_shortfalls:
            return cast(
                dict[str, object],
                RecordReadBackOutput(
                    status="BLOCKED",
                    order_id=order.order_id,
                    order_status=order.status,
                    reason="LOW_STOCK",
                    message="Cannot read back while stock shortfalls exist.",
                ).model_dump(mode="json"),
            )
        if not snapshot.credit_approved and not snapshot.approved_override:
            return cast(
                dict[str, object],
                RecordReadBackOutput(
                    status="BLOCKED",
                    order_id=order.order_id,
                    order_status=order.status,
                    reason="CREDIT_LIMIT_EXCEEDED",
                    excess_amount_paise=snapshot.excess_amount_paise,
                    message="Credit limit exceeded; route order to manager approval first.",
                ).model_dump(mode="json"),
            )
        if snapshot.discount_breaches and not snapshot.approved_override:
            return cast(
                dict[str, object],
                RecordReadBackOutput(
                    status="BLOCKED",
                    order_id=order.order_id,
                    order_status=order.status,
                    reason="DISCOUNT_ABOVE_POLICY",
                    message="Extra discount exceeds policy; route order to manager approval first.",
                ).model_dump(mode="json"),
            )

        if order.status == OrderStatus.DRAFT:
            next_status = transition_order(order.status, OrderStatus.CONFIRMING)
            order = repo.set_order_status(self._db, order.order_id, next_status)
        elif order.status != OrderStatus.CONFIRMING:
            raise InvalidOrderTransitionError(
                f"Cannot record read-back for order in status {order.status.value}"
            )

        self.read_back_spoken = True
        repo.append_audit_event(
            self._db,
            order.order_id,
            event_type="READ_BACK_SPOKEN",
            payload={
                "spoken_summary": payload.spoken_summary.strip(),
                "total_paise": order.total_paise,
                "line_count": len(order.lines),
            },
        )
        return cast(
            dict[str, object],
            RecordReadBackOutput(
                status="OK",
                order_id=order.order_id,
                order_status=order.status,
                total_paise=order.total_paise,
                message="Read-back recorded; awaiting explicit spoken confirmation from rep.",
            ).model_dump(mode="json"),
        )

    def _handle_commit_order(self, args: dict[str, object]) -> dict[str, object]:
        payload = CommitOrderInput.model_validate(args)
        order = self._require_active_order()
        if order is None:
            return cast(
                dict[str, object],
                CommitOrderOutput(
                    status="BLOCKED",
                    reason="NO_ACTIVE_ORDER",
                    message="No active order to commit.",
                ).model_dump(mode="json"),
            )
        if order.status != OrderStatus.CONFIRMING:
            return cast(
                dict[str, object],
                CommitOrderOutput(
                    status="BLOCKED",
                    order_id=order.order_id,
                    order_status=order.status,
                    reason="ORDER_NOT_CONFIRMING",
                    message=(
                        f"Order in status {order.status.value} cannot be committed without "
                        "read-back or approval."
                    ),
                ).model_dump(mode="json"),
            )
        if not self.read_back_spoken or not repo.has_read_back_for_current_draft(
            self._db, order.order_id
        ):
            return cast(
                dict[str, object],
                CommitOrderOutput(
                    status="BLOCKED",
                    order_id=order.order_id,
                    order_status=order.status,
                    reason="READ_BACK_REQUIRED",
                    message="Complete read-back must be spoken and recorded before committing.",
                ).model_dump(mode="json"),
            )

        snapshot = self._evaluate_order_snapshot(order)
        if snapshot.stock_shortfalls:
            return cast(
                dict[str, object],
                CommitOrderOutput(
                    status="BLOCKED",
                    order_id=order.order_id,
                    order_status=order.status,
                    reason="LOW_STOCK",
                    message="Stock is no longer sufficient to commit this order.",
                ).model_dump(mode="json"),
            )
        if snapshot.unresolved_approval:
            return cast(
                dict[str, object],
                CommitOrderOutput(
                    status="BLOCKED",
                    order_id=order.order_id,
                    order_status=order.status,
                    reason="APPROVAL_REQUIRED",
                    message="Unresolved policy breach requires manager approval before commit.",
                ).model_dump(mode="json"),
            )

        # Record spoken confirmation before transitioning to COMMITTED per AGENTS.md §2 rule 3.
        repo.append_audit_event(
            self._db,
            order.order_id,
            event_type="CONFIRMATION_RECEIVED",
            payload={"confirmation_phrase": payload.confirmation_phrase.strip()},
        )
        next_status = transition_order(order.status, OrderStatus.COMMITTED, confirmed_by_voice=True)
        repo.set_order_status(self._db, order.order_id, next_status)
        repo.apply_order_commit_effects(self._db, order.order_id)
        invoice = repo.create_invoice(self._db, order.order_id)
        repo.append_audit_event(
            self._db,
            order.order_id,
            event_type="ORDER_COMMITTED",
            payload={"invoice_id": invoice.invoice_id, "total_paise": invoice.total_paise},
        )
        self.read_back_spoken = False

        return cast(
            dict[str, object],
            CommitOrderOutput(
                status="OK",
                order_id=order.order_id,
                order_status=OrderStatus.COMMITTED,
                invoice=invoice,
                message=f"Order {order.order_id} committed; invoice {invoice.invoice_id} created.",
            ).model_dump(mode="json"),
        )

    def _handle_cancel_order(self, args: dict[str, object]) -> dict[str, object]:
        payload = CancelOrderInput.model_validate(args)
        order = self._require_active_order()
        if order is None:
            return cast(
                dict[str, object],
                CancelOrderOutput(
                    status="BLOCKED",
                    reason="NO_ACTIVE_ORDER",
                    message="No active order to cancel.",
                ).model_dump(mode="json"),
            )
        if order.status not in EDITABLE_STATUSES:
            return cast(
                dict[str, object],
                CancelOrderOutput(
                    status="BLOCKED",
                    order_id=order.order_id,
                    order_status=order.status,
                    reason="CANNOT_CANCEL_STATUS",
                    message=f"Cannot cancel order in status {order.status.value}.",
                ).model_dump(mode="json"),
            )

        next_status = transition_order(order.status, OrderStatus.REJECTED)
        repo.set_order_status(self._db, order.order_id, next_status)
        repo.append_audit_event(
            self._db,
            order.order_id,
            event_type="ORDER_CANCELLED",
            payload={"reason": payload.reason.strip()},
        )
        self.read_back_spoken = False

        return cast(
            dict[str, object],
            CancelOrderOutput(
                status="OK",
                order_id=order.order_id,
                order_status=OrderStatus.REJECTED,
                message=f"Order {order.order_id} cancelled.",
            ).model_dump(mode="json"),
        )
