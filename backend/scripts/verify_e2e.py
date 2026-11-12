"""One-off E2E REST API verification script using FastAPI TestClient."""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy import event as sqla_event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.erp.models import Base
from app.erp.seed import seed_database


def _make_seeded_engine() -> Engine:
    engine: Engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @sqla_event.listens_for(engine, "connect")
    def _fks(dbapi_conn: Any, _record: Any) -> None:
        if isinstance(dbapi_conn, sqlite3.Connection):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_database(session)
        session.commit()
    return engine


def _call_tool(
    client: TestClient,
    session_id: str,
    name: str,
    args: dict[str, Any],
    ok_statuses: set[str],
) -> dict[str, Any]:
    t0 = time.perf_counter()
    resp = client.post(
        f"/api/sessions/{session_id}/tool",
        json={"name": name, "args": args},
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000)

    if resp.status_code != 200:
        print(
            f"[FAIL] step={name} http_status={resp.status_code} "
            f"latency_ms={elapsed_ms} detail={resp.text}"
        )
        sys.exit(1)

    body: dict[str, Any] = resp.json()
    result: dict[str, Any] = body["result"]
    trace_entries: list[dict[str, Any]] = body.get("trace_entries", [])
    latency_ms = trace_entries[-1]["latency_ms"] if trace_entries else elapsed_ms
    status = str(result.get("status", "UNKNOWN"))

    print(f"tool={name} status={status} latency_ms={latency_ms}")

    if status not in ok_statuses:
        print(
            f"[FAIL] step={name} returned status={status} "
            f"reason={result.get('reason')} message={result.get('message')} "
            f"result={result}"
        )
        sys.exit(1)

    return result


def main() -> int:
    engine = _make_seeded_engine()
    orig_engine = main_module._engine
    orig_sessions = main_module._sessions

    main_module._engine = engine
    main_module._sessions = {}
    main_module._trace_hub._entries.clear()
    main_module._trace_hub._next_event_id = 1

    try:
        with TestClient(main_module.app, raise_server_exceptions=True) as client:
            t0 = time.perf_counter()
            sess_resp = client.post("/api/sessions", json={"rep_id": "REP-01"})
            sess_ms = round((time.perf_counter() - t0) * 1000)
            if sess_resp.status_code != 200:
                print(
                    f"[FAIL] step=create_session http_status={sess_resp.status_code} "
                    f"latency_ms={sess_ms} detail={sess_resp.text}"
                )
                return 1
            session_id: str = sess_resp.json()["session_id"]
            print(f"tool=create_session status=OK latency_ms={sess_ms} session_id={session_id}")

            outlet_res = _call_tool(
                client,
                session_id,
                "resolve_outlet",
                {"query": "sharma ji"},
                ok_statuses={"MATCHED"},
            )
            order_id: str = outlet_res["order_id"]

            _call_tool(
                client,
                session_id,
                "add_or_update_line",
                {"utterance_or_product": "5 cartons bhoomi salt 1kg"},
                ok_statuses={"OK"},
            )

            _call_tool(
                client,
                session_id,
                "record_read_back",
                {"spoken_summary": "Sharma ji: 5 cartons Bhoomi Salt 1kg"},
                ok_statuses={"OK"},
            )

            _call_tool(
                client,
                session_id,
                "commit_order",
                {"confirmation_phrase": "haan confirm"},
                ok_statuses={"OK"},
            )

            order_resp = client.get(f"/api/orders/{order_id}")
            if order_resp.status_code != 200:
                print(
                    f"[FAIL] step=get_order http_status={order_resp.status_code} "
                    f"detail={order_resp.text}"
                )
                return 1
            order_detail = order_resp.json()

            trace_resp = client.get(f"/api/trace?session_id={session_id}")
            if trace_resp.status_code != 200:
                print(
                    f"[FAIL] step=get_trace http_status={trace_resp.status_code} "
                    f"detail={trace_resp.text}"
                )
                return 1
            traces = trace_resp.json()

            order_status = order_detail["order"]["status"]
            invoice = order_detail.get("invoice")
            invoice_id = invoice["invoice_id"] if invoice else None

            print(f"final_order_status={order_status}")
            print(f"invoice_id={invoice_id}")
            print(f"trace_count={len(traces)}")
            return 0 if order_status == "COMMITTED" and invoice_id else 1
    finally:
        main_module._engine = orig_engine
        main_module._sessions = orig_sessions
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

