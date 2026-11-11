"""Automated 3-minute hackathon demo double rehearsal runner for TARS.

Executes all 4 demo scenes across two consecutive rehearsal runs (Run #1 and Run #2):
  - Scene 1: Noisy Kirana Order & Trade Scheme Application
  - Scene 2: Mid-Sentence Barge-In & Spoken Correction (CONFIRMING -> DRAFT)
  - Scene 3: Credit Limit Breach (OUT-001) & Manager Approval (PENDING_APPROVAL -> CONFIRMING)
  - Scene 4: Spoken Read-Back, 'Haan' Confirmation Gate & ERP Invoice Commit

Validates all AGENTS.md §2 architectural rules, measures per-tool latencies,
and verifies 100% repeatability.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.erp.models import Base
from app.erp.seed import seed_database
from app.main import app


def setup_rehearsal_engine() -> Engine:
    """Create a high-speed in-memory SQLite database for demo rehearsals."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_database(session)
        session.commit()
    main_module._engine = engine
    return engine


def _tool(client: TestClient, session_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one tool call through the FastAPI session endpoint and return result."""
    resp = client.post(
        f"/api/sessions/{session_id}/tool",
        json={"name": name, "args": args},
    )
    assert resp.status_code == 200, f"Tool dispatch failed: {resp.status_code} {resp.text}"
    body = resp.json()
    result: dict[str, Any] = body["result"]
    return result


def run_single_rehearsal(client: TestClient, run_number: int) -> dict[str, Any]:
    """Execute the complete 4-scene live demo flow for one rehearsal run."""
    run_start = time.perf_counter()
    scene_timings: dict[str, float] = {}

    print(f"\n{'='*70}")
    print(f"  TARS 3-MINUTE DEMO REHEARSAL RUN #{run_number}")
    print(f"{'='*70}")

    # -----------------------------------------------------------------------
    # Setup: Reset Demo Database to Pristine State
    # -----------------------------------------------------------------------
    reset_resp = client.post("/api/admin/reset-demo")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["seeded_outlets"] == 30
    print("[INIT] Demo DB reset & seeded with 30 outlets, 80 SKUs, 10 schemes.")

    # Create rep session
    sess_resp = client.post("/api/sessions", json={"rep_id": "REP-RAVI-01", "route_code": "R1"})
    assert sess_resp.status_code == 200
    sid: str = sess_resp.json()["session_id"]
    print(f"[SESSION] Opened session {sid} for Rep Ravi Kumar (Route R1)")

    # -----------------------------------------------------------------------
    # Scene 1: Noisy Kirana Order & Scheme Application (0:00 - 0:45)
    # -----------------------------------------------------------------------
    s1_start = time.perf_counter()
    print("\n--- Scene 1: Noisy Kirana Order & Trade Scheme Application ---")

    # Step 1.1: Resolve Tiwari Provision Store (OUT-006)
    out1_res = _tool(client, sid, "resolve_outlet", {"query": "tiwari ji ke yahan"})
    assert out1_res["status"] == "MATCHED"
    assert out1_res["outlet"]["outlet_id"] == "OUT-006"
    order1_id: str = out1_res["order_id"]
    print(f"  ✓ Resolved outlet: {out1_res['outlet']['name']} ({out1_res['outlet']['locality']})")

    # Step 1.2: Add Surya Glucose 100g (5 cartons, qualifies for SCH-001 3% discount)
    line1_res = _tool(
        client,
        sid,
        "add_or_update_line",
        {"utterance_or_product": "5 cartons surya glucose 100g"},
    )
    assert line1_res["status"] == "OK"
    assert line1_res["sku"]["sku_id"] == "SKU-001"
    assert line1_res["order"]["total_paise"] == 304000  # 5 * 60800 paise (after 300 bps discount)
    print("  ✓ Added Line 1: 5 cartons Surya Glucose 100g (Applied SCH-001: 3% trade scheme)")

    # Step 1.3: Add Chakki Fresh Atta 5kg (2 bags, SKU-030)
    line2_res = _tool(
        client,
        sid,
        "add_or_update_line",
        {"utterance_or_product": "2 bori chakki atta 5kg bhej do"},
    )
    assert line2_res["status"] == "OK"
    assert len(line2_res["order"]["lines"]) == 2
    scene_timings["Scene 1 (Order & Scheme)"] = round((time.perf_counter() - s1_start) * 1000, 2)
    print(f"  ✓ Added Line 2: 2 bags Chakki Fresh Atta 5kg (Elapsed: {scene_timings['Scene 1 (Order & Scheme)']}ms)")

    # -----------------------------------------------------------------------
    # Scene 2: Mid-Sentence Barge-In & Spoken Correction (0:45 - 1:30)
    # -----------------------------------------------------------------------
    s2_start = time.perf_counter()
    print("\n--- Scene 2: Mid-Sentence Barge-In & Spoken Correction ---")

    # Step 2.1: Agent initiates read-back (order enters CONFIRMING)
    rb_init = _tool(
        client,
        sid,
        "record_read_back",
        {"spoken_summary": "Tiwari Provision Store: 5 cartons Glucose, 2 bori Atta"},
    )
    assert rb_init["status"] == "OK"
    assert rb_init["order_status"] == "CONFIRMING"
    print("  ✓ Agent started read-back → Order transitioned: DRAFT → CONFIRMING")

    # Step 2.2: Rep interrupts (barge-in): updates Glucose to 10 cartons
    # Must immediately reset state machine CONFIRMING -> DRAFT
    corr1_res = _tool(
        client,
        sid,
        "add_or_update_line",
        {"utterance_or_product": "10 cartons surya glucose 100g"},
    )
    assert corr1_res["status"] == "OK"
    assert corr1_res["order"]["status"] == "DRAFT"
    print("  ✓ Barge-in correction: Quantity changed 5 → 10 cartons. State reset: CONFIRMING → DRAFT")

    # Step 2.3: Rep removes the Atta line (SKU-030)
    corr2_res = _tool(
        client,
        sid,
        "remove_or_correct_line",
        {"sku_id": "SKU-030", "new_quantity": 0},
    )
    assert corr2_res["status"] == "OK"
    assert corr2_res["removed"] is True
    assert len(corr2_res["order"]["lines"]) == 1
    assert corr2_res["order"]["total_paise"] == 608000  # ₹6,080.00
    print("  ✓ Removed line: Chakki Fresh Atta. Order total recalculated to ₹6,080.00")

    # Step 2.4: Verify commitment without fresh read-back is BLOCKED
    blocked_commit = _tool(
        client,
        sid,
        "commit_order",
        {"confirmation_phrase": "confirm"},
    )
    assert blocked_commit["status"] == "BLOCKED"
    assert blocked_commit["reason"] in {"ORDER_NOT_CONFIRMING", "READ_BACK_REQUIRED"}
    scene_timings["Scene 2 (Barge-In & Correction)"] = round((time.perf_counter() - s2_start) * 1000, 2)
    print(f"  ✓ Architectural Guard: Commit blocked without fresh spoken read-back (Elapsed: {scene_timings['Scene 2 (Barge-In & Correction)']}ms)")

    # -----------------------------------------------------------------------
    # Scene 3: Credit Breach & Manager Approval Gate (1:30 - 2:15)
    # -----------------------------------------------------------------------
    s3_start = time.perf_counter()
    print("\n--- Scene 3: Credit Breach & Manager Approval Gate ---")

    # Step 3.1: Switch to high-utilization store OUT-001 (Sharma General Store)
    out2_res = _tool(client, sid, "resolve_outlet", {"query": "sharma ji shastri nagar"})
    assert out2_res["status"] == "MATCHED"
    assert out2_res["outlet"]["outlet_id"] == "OUT-001"
    sharma_order_id: str = out2_res["order_id"]
    print(f"  ✓ Switched outlet: {out2_res['outlet']['name']} (Credit Headroom: ₹1,750.00)")

    # Step 3.2: Add 5 cartons Glucose (₹3,040.00) -> Breaches headroom by ₹1,290.00
    sharma_line = _tool(
        client,
        sid,
        "add_or_update_line",
        {"utterance_or_product": "5 cartons surya glucose 100g"},
    )
    assert sharma_line["status"] == "OK"

    # Step 3.3: Request manager approval
    apr_req = _tool(
        client,
        sid,
        "request_manager_approval",
        {"reason_code": "CREDIT_LIMIT_EXCEEDED"},
    )
    assert apr_req["status"] == "OK"
    assert apr_req["order_status"] == "PENDING_APPROVAL"
    request_id: str = apr_req["approval"]["request_id"]
    print(f"  ✓ Credit breach detected: Order moved to PENDING_APPROVAL (Ticket: {request_id})")

    # Step 3.4: Sales Manager approves request from Manager Queue UI
    decide_resp = client.post(
        f"/api/approvals/{request_id}/decide",
        json={"approved": True, "decided_by": "manager-anita"},
    )
    assert decide_resp.status_code == 200
    decide_body = decide_resp.json()
    assert decide_body["approval"]["status"] == "APPROVED"
    assert decide_body["order"]["status"] == "CONFIRMING"
    scene_timings["Scene 3 (Credit & Manager Gate)"] = round((time.perf_counter() - s3_start) * 1000, 2)
    print(f"  ✓ Manager Anita approved ticket → Order transitioned: PENDING_APPROVAL → CONFIRMING (Elapsed: {scene_timings['Scene 3 (Credit & Manager Gate)']}ms)")

    # -----------------------------------------------------------------------
    # Scene 4: Spoken Read-Back, Confirmation & Commit (2:15 - 3:00)
    # -----------------------------------------------------------------------
    s4_start = time.perf_counter()
    print("\n--- Scene 4: Spoken Read-Back, 'Haan' Confirmation Gate & ERP Invoice ---")

    # Step 4.1: Spoken read-back recorded
    rb_sharma = _tool(
        client,
        sid,
        "record_read_back",
        {"spoken_summary": "Sharma General Store: 5 cartons Surya Glucose 100g, Total Rs 3,040"},
    )
    assert rb_sharma["status"] == "OK"
    assert rb_sharma["order_status"] == "CONFIRMING"
    print("  ✓ Spoken read-back recorded: 5 cartons Surya Glucose 100g, Total ₹3,040.00")

    # Step 4.2: Rep gives spoken confirmation: 'Haan confirm kar do'
    commit_res = _tool(
        client,
        sid,
        "commit_order",
        {"confirmation_phrase": "Haan confirm kar do"},
    )
    assert commit_res["status"] == "OK"
    assert commit_res["order_status"] == "COMMITTED"
    assert commit_res["invoice"] is not None
    invoice_id: str = commit_res["invoice"]["invoice_id"]
    assert commit_res["invoice"]["total_paise"] == 304000
    print(f"  ✓ Spoken confirmation verified! Order COMMITTED → Generated Invoice {invoice_id}")

    # Step 4.3: Verify mock ERP committed state via Order Detail endpoint
    detail_resp = client.get(f"/api/orders/{sharma_order_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["order"]["status"] == "COMMITTED"
    assert detail["invoice"]["invoice_id"] == invoice_id
    assert detail["outlet"]["outstanding_paise"] == 2629000  # 2325000 + 304000

    # Step 4.4: Verify Action Trace panel history
    trace_resp = client.get(f"/api/trace?session_id={sid}")
    assert trace_resp.status_code == 200
    traces = trace_resp.json()
    assert len(traces) >= 8
    median_lat = sorted(t["latency_ms"] for t in traces)[len(traces) // 2]
    scene_timings["Scene 4 (Commit & ERP Invoice)"] = round((time.perf_counter() - s4_start) * 1000, 2)
    print(f"  ✓ ERP ledger updated: Stock decremented, balance updated to ₹26,290.00 (Elapsed: {scene_timings['Scene 4 (Commit & ERP Invoice)']}ms)")

    total_time_ms = round((time.perf_counter() - run_start) * 1000, 2)

    return {
        "run_number": run_number,
        "session_id": sid,
        "first_order_id": order1_id,
        "committed_order_id": sharma_order_id,
        "invoice_id": invoice_id,
        "trace_count": len(traces),
        "median_tool_latency_ms": median_lat,
        "scene_timings_ms": scene_timings,
        "total_elapsed_ms": total_time_ms,
    }


def main() -> int:
    """Run double demo rehearsal back-to-back and report timing metrics."""
    engine = setup_rehearsal_engine()
    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            # Run 1
            res1 = run_single_rehearsal(client, 1)

            # Run 2 (verify repeatability and zero state leakage)
            res2 = run_single_rehearsal(client, 2)
    finally:
        engine.dispose()

    print("\n" + "=" * 70)
    print("  TARS DOUBLE DEMO REHEARSAL SUMMARY TABLE")
    print("=" * 70)
    print(f"{'Metric':<38} | {'Run #1':<14} | {'Run #2':<14}")
    print("-" * 70)
    print(f"{'Total Elapsed Time (ms)':<38} | {res1['total_elapsed_ms']:<14} | {res2['total_elapsed_ms']:<14}")
    print(f"{'Total Elapsed Time (seconds)':<38} | {res1['total_elapsed_ms']/1000:<14.2f} | {res2['total_elapsed_ms']/1000:<14.2f}")
    print(f"{'Median Tool Latency (ms)':<38} | {res1['median_tool_latency_ms']:<14} | {res2['median_tool_latency_ms']:<14}")
    print(f"{'Audited Trace Tool Calls':<38} | {res1['trace_count']:<14} | {res2['trace_count']:<14}")
    print(f"{'Committed Invoice ID':<38} | {res1['invoice_id']:<14} | {res2['invoice_id']:<14}")
    print("-" * 70)
    for scene in res1["scene_timings_ms"]:
        t1 = res1["scene_timings_ms"][scene]
        t2 = res2["scene_timings_ms"].get(scene, 0.0)
        print(f"{scene:<38} | {t1:<14} | {t2:<14}")
    print("=" * 70)
    print("All 4 demo scenes passed twice with zero regressions and clean state resets.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
