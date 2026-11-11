from collections.abc import Generator
from datetime import date
from typing import Any

import pytest
from google.genai import types
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.agent import TOOL_NAMES, OrderOrchestrator, get_gemini_tools
from app.approvals import resolve_approval_request
from app.erp import repo
from app.erp.models import ApprovalStatus, Base, OrderStatus
from app.erp.seed import seed_database
from app.trace import TraceStreamHub


def _enable_sqlite_fks(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest.fixture()
def seeded_db() -> Generator[Session, None, None]:
    engine: Engine = create_engine("sqlite:///:memory:", future=True)
    event.listen(engine, "connect", _enable_sqlite_fks)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        seed_database(session)
        yield session

    engine.dispose()


def test_gemini_tools_declarations_cover_all_8_tools() -> None:
    tools = get_gemini_tools()
    assert len(tools) == 1
    assert isinstance(tools[0], types.Tool)
    decls = tools[0].function_declarations
    assert decls is not None
    assert [decl.name for decl in decls] == list(TOOL_NAMES)

    for decl in decls:
        dumped = decl.model_dump(by_alias=True, exclude_none=True)
        assert dumped["name"] in TOOL_NAMES
        assert "parameters" in dumped


@pytest.mark.asyncio
async def test_trace_logger_records_audit_events_and_publishes_live_queue(
    seeded_db: Session,
) -> None:
    hub = TraceStreamHub()
    queue = hub.subscribe()
    orch = OrderOrchestrator(
        seeded_db,
        session_id="sess-trace-1",
        rep_id="REP-01",
        as_of=date(2026, 11, 15),
        trace_hub=hub,
    )

    ok_res = orch.dispatch_tool("resolve_outlet", {"query": "tiwari ji"})
    bad_args_res = orch.dispatch_tool("resolve_outlet", {"query": "   "})
    unknown_res = orch.dispatch_tool("nonexistent_tool", {"x": 1})

    assert ok_res["status"] == "MATCHED"
    assert bad_args_res["status"] == "ERROR"
    assert unknown_res["status"] == "ERROR"

    entries = hub.list_entries(session_id="sess-trace-1")
    assert len(entries) == 3
    assert [e.tool_name for e in entries] == [
        "resolve_outlet",
        "resolve_outlet",
        "nonexistent_tool",
    ]
    assert [e.status for e in entries] == ["OK", "ERROR", "ERROR"]
    assert all(e.latency_ms >= 0 for e in entries)

    streamed = [queue.get_nowait(), queue.get_nowait(), queue.get_nowait()]
    assert [item.event_id for item in streamed] == [1, 2, 3]

    hub.unsubscribe(queue)
    orch.dispatch_tool("check_order_rules", {})
    assert queue.empty()

    audit_events = repo.list_audit_events(seeded_db)
    tool_event_types = [e.event_type for e in audit_events if e.event_type.startswith("TOOL:")]
    assert tool_event_types == [
        "TOOL:resolve_outlet",
        "TOOL:resolve_outlet",
        "TOOL:nonexistent_tool",
        "TOOL:check_order_rules",
    ]


def test_happy_path_read_back_gate_commit_and_invoice(seeded_db: Session) -> None:
    orch = OrderOrchestrator(
        seeded_db,
        session_id="sess-happy",
        rep_id="REP-02",
        as_of=date(2026, 11, 15),
    )

    outlet_res = orch.dispatch_tool("resolve_outlet", {"query": "tiwari ji"})
    assert outlet_res["status"] == "MATCHED"
    order_id = str(outlet_res["order_id"])

    # Cannot commit directly without read-back
    blocked_commit = orch.dispatch_tool("commit_order", {"confirmation_phrase": "Haan confirm"})
    assert blocked_commit["status"] == "BLOCKED"

    # Near-miss qty=4 triggers scheme upsell hint for SCH-001 (min_qty=5 -> 500 bps)
    near_miss = orch.dispatch_tool(
        "add_or_update_line",
        {"utterance_or_product": "4 cartons surya glucose 100g"},
    )
    assert near_miss["status"] == "OK"
    assert near_miss["upsell_hint"] is not None

    # Update to qty=5 unlocks 5% (500 bps) scheme: 5 * 64000 * 9500 // 10000 = 304000
    updated = orch.dispatch_tool(
        "add_or_update_line",
        {"utterance_or_product": "5 cartons surya glucose 100g"},
    )
    assert updated["status"] == "OK"
    order_payload = updated["order"]
    assert isinstance(order_payload, dict)
    assert len(order_payload["lines"]) == 1
    assert order_payload["total_paise"] == 304000

    rules_res = orch.dispatch_tool("check_order_rules", {})
    assert rules_res["status"] == "OK"
    assert rules_res["ready_for_read_back"] is True

    read_back_res = orch.dispatch_tool(
        "record_read_back",
        {
            "spoken_summary": (
                "Tiwari Provision Store: 5 cartons Surya Crisp Glucose Biscuit 100g, "
                "total Rs 3040. Confirm karein?"
            )
        },
    )
    assert read_back_res["status"] == "OK"
    assert read_back_res["order_status"] == OrderStatus.CONFIRMING.value

    commit_res = orch.dispatch_tool("commit_order", {"confirmation_phrase": "Haan confirm"})
    assert commit_res["status"] == "OK"
    assert commit_res["order_status"] == OrderStatus.COMMITTED.value
    invoice = commit_res["invoice"]
    assert isinstance(invoice, dict)
    assert invoice["total_paise"] == 304000

    sku_after = repo.get_sku(seeded_db, "SKU-001")
    outlet_after = repo.get_outlet(seeded_db, "OUT-006")
    assert sku_after is not None and sku_after.stock_units == 135  # 140 - 5
    assert outlet_after is not None and outlet_after.outstanding_paise == 950000 + 304000

    event_types = [e.event_type for e in repo.list_audit_events(seeded_db, order_id)]
    assert "READ_BACK_SPOKEN" in event_types
    assert "CONFIRMATION_RECEIVED" in event_types
    assert "ORDER_COMMITTED" in event_types
    assert (
        event_types.index("READ_BACK_SPOKEN")
        < event_types.index("CONFIRMATION_RECEIVED")
        < event_types.index("ORDER_COMMITTED")
    )


def test_low_confidence_never_guesses_and_blocks_low_stock(seeded_db: Session) -> None:
    orch = OrderOrchestrator(
        seeded_db,
        session_id="sess-ambig",
        rep_id="REP-02",
        as_of=date(2026, 11, 15),
    )

    ambig_outlet = orch.dispatch_tool("resolve_outlet", {"query": "sharma"})
    assert ambig_outlet["status"] == "AMBIGUOUS"
    assert orch.active_order_id is None

    matched_outlet = orch.dispatch_tool("resolve_outlet", {"query": "tiwari ji"})
    assert matched_outlet["status"] == "MATCHED"
    order_id = str(matched_outlet["order_id"])

    # "surya glucose" without pack matches both 100g and 50g Glucose Biscuit -> AMBIGUOUS + needs_pack=True
    ambig_sku = orch.dispatch_tool(
        "add_or_update_line",
        {"utterance_or_product": "5 cartons surya glucose"},
    )
    assert ambig_sku["status"] == "AMBIGUOUS"
    assert ambig_sku["needs_pack"] is True

    # Low stock block (SKU-001 has 140 units; requesting 200 units must block)
    low_stock = orch.dispatch_tool(
        "add_or_update_line",
        {"utterance_or_product": "200 cartons surya glucose 100g"},
    )
    assert low_stock["status"] == "BLOCKED"
    assert low_stock["reason"] == "LOW_STOCK"

    order = repo.get_order(seeded_db, order_id)
    assert order is not None
    assert order.lines == []
    assert order.total_paise == 0


def test_credit_breach_routes_through_manager_approval_then_commit(
    seeded_db: Session,
) -> None:
    orch = OrderOrchestrator(
        seeded_db,
        session_id="sess-credit",
        rep_id="REP-01",
        as_of=date(2026, 11, 15),
    )

    # OUT-001 has credit_limit=2,500,000 and outstanding=2,325,000 -> 175,000 headroom
    outlet_res = orch.dispatch_tool("resolve_outlet", {"query": "sharma ji"})
    assert outlet_res["status"] == "MATCHED"
    order_id = str(outlet_res["order_id"])

    line_res = orch.dispatch_tool(
        "add_or_update_line",
        {"utterance_or_product": "5 cartons surya glucose 100g"},
    )
    assert line_res["status"] == "OK"  # 304,000 paise > 175,000 headroom

    blocked_rb = orch.dispatch_tool("record_read_back", {"spoken_summary": "Summary"})
    assert blocked_rb["status"] == "BLOCKED"
    assert blocked_rb["reason"] == "CREDIT_LIMIT_EXCEEDED"

    blocked_commit = orch.dispatch_tool("commit_order", {"confirmation_phrase": "Haan"})
    assert blocked_commit["status"] == "BLOCKED"

    apr_res = orch.dispatch_tool(
        "request_manager_approval",
        {"reason_code": "CREDIT_LIMIT_EXCEEDED"},
    )
    assert apr_res["status"] == "OK"
    assert apr_res["order_status"] == OrderStatus.PENDING_APPROVAL.value
    approval_payload = apr_res["approval"]
    assert isinstance(approval_payload, dict)
    request_id = str(approval_payload["request_id"])

    # Cannot commit while waiting for human approval
    pending_commit = orch.dispatch_tool("commit_order", {"confirmation_phrase": "Haan"})
    assert pending_commit["status"] == "BLOCKED"

    # Human manager approves
    approval_dto, order_dto = resolve_approval_request(
        seeded_db,
        request_id,
        approved=True,
        decided_by="mgr-anita",
    )
    seeded_db.commit()
    assert approval_dto.status == ApprovalStatus.APPROVED
    assert order_dto.status == OrderStatus.CONFIRMING

    # Must still speak read-back BEFORE committing even after manager approval
    pre_read_commit = orch.dispatch_tool("commit_order", {"confirmation_phrase": "Haan"})
    assert pre_read_commit["status"] == "BLOCKED"
    assert pre_read_commit["reason"] == "READ_BACK_REQUIRED"

    rb_res = orch.dispatch_tool(
        "record_read_back",
        {"spoken_summary": "Sharma General Store approved order Rs 3040. Confirm?"},
    )
    assert rb_res["status"] == "OK"

    final_commit = orch.dispatch_tool("commit_order", {"confirmation_phrase": "Haan confirm"})
    assert final_commit["status"] == "OK"
    assert final_commit["order_status"] == OrderStatus.COMMITTED.value

    order_final = repo.get_order(seeded_db, order_id)
    assert order_final is not None and order_final.status == OrderStatus.COMMITTED


def test_discount_policy_breach_and_manager_rejection(seeded_db: Session) -> None:
    orch = OrderOrchestrator(
        seeded_db,
        session_id="sess-reject",
        rep_id="REP-03",
        as_of=date(2026, 11, 15),
    )

    orch.dispatch_tool("resolve_outlet", {"query": "tiwari ji"})
    line_res = orch.dispatch_tool(
        "add_or_update_line",
        {
            "utterance_or_product": "5 cartons surya glucose 100g",
            "extra_discount_bps": 250,
        },
    )
    assert line_res["status"] == "OK"
    assert line_res["policy_breach"] == "DISCOUNT_ABOVE_POLICY"

    rb_blocked = orch.dispatch_tool("record_read_back", {"spoken_summary": "Try read back"})
    assert rb_blocked["status"] == "BLOCKED"
    assert rb_blocked["reason"] == "DISCOUNT_ABOVE_POLICY"

    apr_res = orch.dispatch_tool(
        "request_manager_approval",
        {"reason_code": "DISCOUNT_ABOVE_POLICY"},
    )
    assert apr_res["status"] == "OK"
    request_id = str(apr_res["approval"]["request_id"])  # type: ignore[index]

    approval_dto, order_dto = resolve_approval_request(
        seeded_db,
        request_id,
        approved=False,
        decided_by="mgr-anita",
    )
    seeded_db.commit()
    assert approval_dto.status == ApprovalStatus.REJECTED
    assert order_dto.status == OrderStatus.REJECTED

    assert orch.dispatch_tool("commit_order", {"confirmation_phrase": "Haan"})["status"] == "BLOCKED"
    assert orch.dispatch_tool("cancel_order", {"reason": "Already rejected"})["status"] == "BLOCKED"


def test_spoken_correction_resets_read_back_and_cancel_flows(seeded_db: Session) -> None:
    orch = OrderOrchestrator(
        seeded_db,
        session_id="sess-correct",
        rep_id="REP-04",
        as_of=date(2026, 11, 15),
    )

    outlet_res = orch.dispatch_tool("resolve_outlet", {"query": "tiwari ji"})
    order_id = str(outlet_res["order_id"])

    orch.dispatch_tool(
        "add_or_update_line",
        {"utterance_or_product": "5 cartons surya glucose 100g"},
    )
    rb1 = orch.dispatch_tool("record_read_back", {"spoken_summary": "5 cartons Surya 100g"})
    assert rb1["status"] == "OK"
    assert rb1["order_status"] == OrderStatus.CONFIRMING.value

    # Rep interrupts and changes quantity -> resets CONFIRMING -> DRAFT and clears read-back gate
    corrected = orch.dispatch_tool(
        "remove_or_correct_line",
        {"sku_id": "SKU-001", "new_quantity": 6},
    )
    assert corrected["status"] == "OK"
    assert corrected["removed"] is False
    assert orch.read_back_spoken is False

    order_after_edit = repo.get_order(seeded_db, order_id)
    assert order_after_edit is not None
    assert order_after_edit.status == OrderStatus.DRAFT
    assert order_after_edit.lines[0].quantity == 6

    # Commit blocked until fresh read-back
    assert orch.dispatch_tool("commit_order", {"confirmation_phrase": "Haan"})["status"] == "BLOCKED"

    # Remove line (new_quantity=0) leaves empty order
    removed = orch.dispatch_tool(
        "remove_or_correct_line",
        {"sku_id": "SKU-001", "new_quantity": 0},
    )
    assert removed["status"] == "OK"
    assert removed["removed"] is True
    order_empty = repo.get_order(seeded_db, order_id)
    assert order_empty is not None and order_empty.total_paise == 0

    # Cancel uncommitted draft order
    cancelled = orch.dispatch_tool("cancel_order", {"reason": "Customer changed mind"})
    assert cancelled["status"] == "OK"
    assert cancelled["order_status"] == OrderStatus.REJECTED.value

    # Now place and commit a second order, then verify cancel after COMMITTED is blocked
    orch.dispatch_tool("resolve_outlet", {"query": "tiwari ji"})
    orch.dispatch_tool(
        "add_or_update_line",
        {"utterance_or_product": "5 cartons surya glucose 100g"},
    )
    orch.dispatch_tool("record_read_back", {"spoken_summary": "Second order read-back"})
    committed = orch.dispatch_tool("commit_order", {"confirmation_phrase": "Confirm"})
    assert committed["status"] == "OK"

    cancel_committed = orch.dispatch_tool("cancel_order", {"reason": "Too late"})
    assert cancel_committed["status"] == "BLOCKED"
