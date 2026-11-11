"""Phase 5 backend API endpoint tests (P5-01 to P5-03 backend support).

Covers:
- GET /health preserved regression
- GET /api/outlets, /api/skus, /api/schemes — catalog endpoints
- POST /api/sessions + POST /api/sessions/{id}/tool — session & tool dispatch
- GET /api/approvals + POST /api/approvals/{id}/decide — approval queue
- GET /api/trace — trace entry listing
- GET /api/orders/{order_id} — order detail
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event as sqla_event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.erp.models import Base
from app.erp.seed import seed_database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_seeded_engine() -> Engine:
    from sqlalchemy.pool import StaticPool  # noqa: PLC0415

    engine: Engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # same connection reused — tables persist across sessions
        future=True,
    )

    @sqla_event.listens_for(engine, "connect")
    def _fks(dbapi_conn: Any, _record: Any) -> None:
        import sqlite3  # noqa: PLC0415
        if isinstance(dbapi_conn, sqlite3.Connection):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_database(session)
        session.commit()
    return engine


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    """TestClient backed by a fresh seeded in-memory SQLite DB per test function.

    Uses raise_server_exceptions=False so that endpoint errors propagate as HTTP
    status codes rather than crashing the test runner.  lifespan is NOT triggered
    by default in TestClient when we pass the app directly — tables and seed data
    are pre-created on the patched engine before the client is instantiated.
    """
    import app.main as main_module  # noqa: PLC0415

    engine = _make_seeded_engine()

    # Patch globals *before* TestClient is created
    orig_engine = main_module._engine  # noqa: SLF001
    orig_sessions: dict[str, Any] = main_module._sessions  # noqa: SLF001

    main_module._engine = engine  # noqa: SLF001
    main_module._sessions = {}  # noqa: SLF001
    # Reset trace hub entries
    main_module._trace_hub._entries.clear()  # noqa: SLF001
    main_module._trace_hub._next_event_id = 1  # noqa: SLF001

    # lifespan=False skips the async lifespan context so we control the DB ourselves
    with TestClient(main_module.app, raise_server_exceptions=True) as c:
        yield c

    main_module._engine = orig_engine  # noqa: SLF001
    main_module._sessions = orig_sessions  # noqa: SLF001
    engine.dispose()


# ---------------------------------------------------------------------------
# Health (regression guard — P0-02)
# ---------------------------------------------------------------------------


def test_health_endpoint_still_works(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Catalog endpoints
# ---------------------------------------------------------------------------


def test_list_outlets_returns_30_records(client: TestClient) -> None:
    resp = client.get("/api/outlets")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 30
    first = data[0]
    assert "outlet_id" in first
    assert isinstance(first["credit_limit_paise"], int)


def test_list_skus_returns_80_records(client: TestClient) -> None:
    resp = client.get("/api/skus")
    assert resp.status_code == 200
    assert len(resp.json()) == 80


def test_list_schemes_returns_10_records(client: TestClient) -> None:
    resp = client.get("/api/schemes")
    assert resp.status_code == 200
    assert len(resp.json()) == 10


# ---------------------------------------------------------------------------
# Session endpoints (P5-01)
# ---------------------------------------------------------------------------


def test_create_session_returns_uuid(client: TestClient) -> None:
    resp = client.post("/api/sessions", json={"rep_id": "REP-TEST-01"})
    assert resp.status_code == 200
    body = resp.json()
    assert "session_id" in body
    assert body["rep_id"] == "REP-TEST-01"
    assert len(body["session_id"]) == 36  # UUID4


def test_create_session_missing_rep_id_returns_422(client: TestClient) -> None:
    resp = client.post("/api/sessions", json={})
    assert resp.status_code == 422


def test_tool_dispatch_resolve_outlet_matched(client: TestClient) -> None:
    sess_resp = client.post("/api/sessions", json={"rep_id": "REP-TOOL-01"})
    session_id = sess_resp.json()["session_id"]

    resp = client.post(
        f"/api/sessions/{session_id}/tool",
        json={"name": "resolve_outlet", "args": {"query": "tiwari ji"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["status"] == "MATCHED"
    assert body["session_id"] == session_id
    # Trace entry must be recorded
    assert len(body["trace_entries"]) >= 1
    trace = body["trace_entries"][0]
    assert trace["tool_name"] == "resolve_outlet"
    assert trace["latency_ms"] >= 0
    assert trace["status"] == "OK"


def test_tool_dispatch_unknown_session_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/api/sessions/no-such-session/tool",
        json={"name": "resolve_outlet", "args": {"query": "test"}},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Full order flow: resolve -> add -> read_back -> commit (P5-01 + P5-05)
# ---------------------------------------------------------------------------


def _tool(client: TestClient, session_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    r = client.post(
        f"/api/sessions/{session_id}/tool",
        json={"name": name, "args": args},
    )
    assert r.status_code == 200
    return r.json()["result"]  # type: ignore[no-any-return]


def test_full_happy_path_via_rest(client: TestClient) -> None:
    sess_resp = client.post("/api/sessions", json={"rep_id": "REP-FLOW-01"})
    sid = sess_resp.json()["session_id"]

    outlet_res = _tool(client, sid, "resolve_outlet", {"query": "tiwari ji"})
    assert outlet_res["status"] == "MATCHED"
    order_id = outlet_res["order_id"]

    _tool(client, sid, "add_or_update_line",
          {"utterance_or_product": "5 cartons surya glucose 100g"})

    rb_res = _tool(client, sid, "record_read_back",
                   {"spoken_summary": "Tiwari: 5 cartons Glucose 100g, Rs 3040"})
    assert rb_res["status"] == "OK"

    commit_res = _tool(client, sid, "commit_order", {"confirmation_phrase": "Haan confirm"})
    assert commit_res["status"] == "OK"
    assert commit_res["order_status"] == "COMMITTED"
    assert commit_res["invoice"] is not None
    assert commit_res["invoice"]["total_paise"] == 304000

    # Order detail endpoint
    detail_resp = client.get(f"/api/orders/{order_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["order"]["status"] == "COMMITTED"
    assert detail["invoice"]["total_paise"] == 304000
    assert detail["outlet"]["outlet_id"] is not None


# ---------------------------------------------------------------------------
# Approval queue (P5-02)
# ---------------------------------------------------------------------------


def test_approval_queue_credit_breach_then_decide(client: TestClient) -> None:
    sess_resp = client.post("/api/sessions", json={"rep_id": "REP-APR-01"})
    sid = sess_resp.json()["session_id"]

    # OUT-001 high utilization outlet
    _tool(client, sid, "resolve_outlet", {"query": "sharma ji"})
    _tool(client, sid, "add_or_update_line",
          {"utterance_or_product": "5 cartons surya glucose 100g"})

    apr_res = _tool(client, sid, "request_manager_approval",
                    {"reason_code": "CREDIT_LIMIT_EXCEEDED"})
    assert apr_res["status"] == "OK"
    request_id: str = apr_res["approval"]["request_id"]

    # List pending approvals
    list_resp = client.get("/api/approvals?status=PENDING")
    assert list_resp.status_code == 200
    items = list_resp.json()
    request_ids = [item["approval"]["request_id"] for item in items]
    assert request_id in request_ids
    matching = next(i for i in items if i["approval"]["request_id"] == request_id)
    assert matching["order_total_paise"] > 0
    assert matching["outlet_name"] != ""

    # Manager approves
    decide_resp = client.post(
        f"/api/approvals/{request_id}/decide",
        json={"approved": True, "decided_by": "mgr-anita"},
    )
    assert decide_resp.status_code == 200
    decide_body = decide_resp.json()
    assert decide_body["approval"]["status"] == "APPROVED"
    assert decide_body["order"]["status"] == "CONFIRMING"

    # Rep can now do read-back and commit
    rb_res = _tool(client, sid, "record_read_back",
                   {"spoken_summary": "Sharma: approved order"})
    assert rb_res["status"] == "OK"
    commit_res = _tool(client, sid, "commit_order", {"confirmation_phrase": "Haan"})
    assert commit_res["status"] == "OK"


def test_decide_nonexistent_approval_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/api/approvals/APR-999999/decide",
        json={"approved": True, "decided_by": "mgr"},
    )
    assert resp.status_code == 404


def test_list_all_approvals_no_filter(client: TestClient) -> None:
    resp = client.get("/api/approvals")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_list_approvals_invalid_status_returns_400(client: TestClient) -> None:
    resp = client.get("/api/approvals?status=BOGUS")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Trace endpoint (P5-03)
# ---------------------------------------------------------------------------


def test_trace_endpoint_returns_entries_with_latency(client: TestClient) -> None:
    sess_resp = client.post("/api/sessions", json={"rep_id": "REP-TRACE-01"})
    sid = sess_resp.json()["session_id"]
    _tool(client, sid, "resolve_outlet", {"query": "tiwari ji"})

    trace_resp = client.get(f"/api/trace?session_id={sid}")
    assert trace_resp.status_code == 200
    entries = trace_resp.json()
    assert len(entries) >= 1
    entry = entries[0]
    assert entry["tool_name"] == "resolve_outlet"
    assert entry["latency_ms"] >= 0
    assert entry["status"] in {"OK", "BLOCKED", "ERROR"}


# ---------------------------------------------------------------------------
# Order detail 404
# ---------------------------------------------------------------------------


def test_order_detail_nonexistent_returns_404(client: TestClient) -> None:
    resp = client.get("/api/orders/ORD-DOESNOTEXIST")
    assert resp.status_code == 404
