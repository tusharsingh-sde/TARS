"""TARS FastAPI application — Phase 5 wired backend.

Keeps GET /health intact for existing tests (test_health.py).
Auto-seeds the SQLite DB on startup via lifespan if the outlets table is empty.
All ERP writes go through repo.py per AGENTS.md §2 rule 2.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnected

from app.agent import OrderOrchestrator
from app.approvals import resolve_approval_request
from app.config import get_settings
from app.erp import repo
from app.erp.errors import ERPError, ERPNotFoundError
from app.erp.models import (
    ApprovalRequest,
    ApprovalStatus,
    Base,
    OrderStatus,
    Outlet,
)
from app.erp.schemas import (
    ApprovalDTO,
    InvoiceDTO,
    OrderDTO,
    OutletDTO,
    SchemeDTO,
    SkuDTO,
)
from app.erp.seed import seed_database
from app.trace.stream import TraceEntry, TraceStreamHub
from app.voice.gemini_live import (
    AudioChunkEvent,
    BargeInEvent,
    GeminiLiveBridge,
    ToolCallRequestEvent,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global singletons shared across requests
# ---------------------------------------------------------------------------

_engine = create_engine(
    get_settings().database_url,
    connect_args={"check_same_thread": False},
    future=True,
)

# In-memory session store: session_id -> OrderOrchestrator
_sessions: dict[str, OrderOrchestrator] = {}

# Single shared trace hub for the process
_trace_hub = TraceStreamHub()


def _get_db() -> Generator[Session, None, None]:  # FastAPI dependency
    with Session(_engine) as session:
        yield session


DbDep = Annotated[Session, Depends(_get_db)]


# ---------------------------------------------------------------------------
# Lifespan — auto-seed on startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(_engine)
    with Session(_engine) as session:
        count = session.scalar(select(Outlet).limit(1))
        if count is None:
            logger.info("DB is empty — seeding demo data")
            seed_database(session)
            session.commit()
    yield


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


app = FastAPI(title="TARS", version="0.1.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health (kept for P0-02 test_health.py)
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Response model for the liveness health check endpoint."""

    status: str
    version: str


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return service liveness status and version."""
    return HealthResponse(status="ok", version="0.1.0")


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


class ResetResponse(BaseModel):
    seeded_outlets: int
    message: str


@app.post("/api/admin/reset-demo", response_model=ResetResponse)
async def reset_demo() -> ResetResponse:
    """Drop all data, re-seed the demo DB, and clear in-memory state."""
    global _sessions
    _sessions = {}
    # Reset the trace hub
    _trace_hub._entries.clear()  # internal reset only
    _trace_hub._next_event_id = 1

    # Drop and recreate all tables
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    with Session(_engine) as session:
        seed_database(session)
        session.commit()
        outlet_count = session.scalar(select(Outlet).limit(1))

    logger.info("Demo DB reset and re-seeded")
    return ResetResponse(
        seeded_outlets=30 if outlet_count is not None else 0,
        message="Demo DB reset and re-seeded successfully.",
    )


# ---------------------------------------------------------------------------
# Catalog endpoints
# ---------------------------------------------------------------------------


@app.get("/api/outlets", response_model=list[OutletDTO])
def list_outlets(db: DbDep) -> list[OutletDTO]:
    return repo.list_outlets(db)


@app.get("/api/skus", response_model=list[SkuDTO])
def list_skus(db: DbDep) -> list[SkuDTO]:
    return repo.list_skus(db)


@app.get("/api/schemes", response_model=list[SchemeDTO])
def list_schemes(db: DbDep) -> list[SchemeDTO]:
    return repo.list_all_schemes(db)


# ---------------------------------------------------------------------------
# Order detail
# ---------------------------------------------------------------------------


class OrderDetailResponse(BaseModel):
    order: OrderDTO
    outlet: OutletDTO
    invoice: InvoiceDTO | None = None
    active_approval: ApprovalDTO | None = None


@app.get("/api/orders/{order_id}", response_model=OrderDetailResponse)
def get_order_detail(order_id: str, db: DbDep) -> OrderDetailResponse:
    order = repo.get_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order '{order_id}' not found")
    outlet = repo.get_outlet(db, order.outlet_id)
    if outlet is None:
        raise HTTPException(status_code=404, detail=f"Outlet '{order.outlet_id}' not found")

    invoice: InvoiceDTO | None = None
    if order.status == OrderStatus.COMMITTED:
        invoice = repo.get_invoice_for_order(db, order_id)

    active_approval: ApprovalDTO | None = None
    approvals = repo.list_approvals_for_order(db, order_id)
    pending = [a for a in approvals if a.status == ApprovalStatus.PENDING]
    if pending:
        active_approval = pending[-1]

    return OrderDetailResponse(
        order=order,
        outlet=outlet,
        invoice=invoice,
        active_approval=active_approval,
    )


# ---------------------------------------------------------------------------
# Manager Approval Queue
# ---------------------------------------------------------------------------


class ApprovalWithSummary(BaseModel):
    approval: ApprovalDTO
    order_total_paise: int
    outlet_name: str
    outlet_id: str


class DecideApprovalRequest(BaseModel):
    approved: bool
    decided_by: str = Field(min_length=1)


class DecideApprovalResponse(BaseModel):
    approval: ApprovalDTO
    order: OrderDTO
    message: str


@app.get("/api/approvals", response_model=list[ApprovalWithSummary])
def list_approvals(
    db: DbDep,
    status: str | None = Query(default=None),
) -> list[ApprovalWithSummary]:
    stmt = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc())
    if status:
        try:
            status_filter = ApprovalStatus(status.upper())
            stmt = stmt.where(ApprovalRequest.status == status_filter)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    rows = db.scalars(stmt).all()
    result: list[ApprovalWithSummary] = []
    for row in rows:
        approval = ApprovalDTO.model_validate(row)
        order = repo.get_order(db, approval.order_id)
        outlet_name = "Unknown"
        outlet_id = ""
        order_total_paise = 0
        if order is not None:
            order_total_paise = order.total_paise
            outlet = repo.get_outlet(db, order.outlet_id)
            if outlet is not None:
                outlet_name = outlet.name
                outlet_id = outlet.outlet_id
        result.append(
            ApprovalWithSummary(
                approval=approval,
                order_total_paise=order_total_paise,
                outlet_name=outlet_name,
                outlet_id=outlet_id,
            )
        )
    return result


@app.post(
    "/api/approvals/{request_id}/decide",
    response_model=DecideApprovalResponse,
)
def decide_approval(
    request_id: str,
    body: DecideApprovalRequest,
    db: DbDep,
) -> DecideApprovalResponse:
    try:
        approval, order = resolve_approval_request(
            db,
            request_id,
            approved=body.approved,
            decided_by=body.decided_by,
        )
        db.commit()
    except ERPNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ERPError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    action = "approved" if body.approved else "rejected"
    return DecideApprovalResponse(
        approval=approval,
        order=order,
        message=f"Approval request {request_id} {action} by {body.decided_by}.",
    )


# ---------------------------------------------------------------------------
# Trace endpoints
# ---------------------------------------------------------------------------


@app.get("/api/trace", response_model=list[TraceEntry])
def get_trace(session_id: str | None = Query(default=None)) -> list[TraceEntry]:
    return _trace_hub.list_entries(session_id=session_id)


@app.get("/api/trace/stream")
async def trace_stream(session_id: str | None = Query(default=None)) -> StreamingResponse:
    """Server-Sent Events stream of live TraceEntry JSON objects."""

    async def _sse_generator() -> AsyncIterator[str]:
        queue = _trace_hub.subscribe()
        try:
            # Replay existing entries first
            for entry in _trace_hub.list_entries(session_id=session_id):
                yield f"data: {entry.model_dump_json()}\n\n"
            # Stream new entries
            while True:
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=15.0)
                    if session_id is None or entry.session_id == session_id:
                        yield f"data: {entry.model_dump_json()}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _trace_hub.unsubscribe(queue)

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    rep_id: str = Field(min_length=1)
    route_code: str | None = None


class CreateSessionResponse(BaseModel):
    session_id: str
    rep_id: str
    route_code: str | None = None
    message: str


class ToolDispatchRequest(BaseModel):
    name: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)


class ToolDispatchResponse(BaseModel):
    result: dict[str, Any]
    session_id: str
    trace_entries: list[TraceEntry]


def _get_orchestrator(session_id: str) -> OrderOrchestrator:
    orch = _sessions.get(session_id)
    if orch is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return orch


@app.post("/api/sessions", response_model=CreateSessionResponse)
def create_session(body: CreateSessionRequest) -> CreateSessionResponse:
    session_id = str(uuid.uuid4())
    with Session(_engine) as db:
        orch = OrderOrchestrator(
            db,
            session_id=session_id,
            rep_id=body.rep_id,
            as_of=date.today(),
            trace_hub=_trace_hub,
            auto_commit=True,
        )
        _sessions[session_id] = orch
    return CreateSessionResponse(
        session_id=session_id,
        rep_id=body.rep_id,
        route_code=body.route_code,
        message=f"Session {session_id} created for rep {body.rep_id}.",
    )


@app.post("/api/sessions/{session_id}/tool", response_model=ToolDispatchResponse)
def dispatch_tool(
    session_id: str,
    body: ToolDispatchRequest,
    db: DbDep,
) -> ToolDispatchResponse:
    orch = _sessions.get(session_id)
    if orch is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    # Re-attach orchestrator to this request's DB session
    orch._db = db  # necessary for per-request sessions

    before_count = len(_trace_hub.list_entries(session_id=session_id))
    result = orch.dispatch_tool(body.name, body.args)
    new_entries = _trace_hub.list_entries(session_id=session_id)[before_count:]

    return ToolDispatchResponse(
        result=result,
        session_id=session_id,
        trace_entries=new_entries,
    )


# ---------------------------------------------------------------------------
# WebSocket voice bridge
# ---------------------------------------------------------------------------


async def _forward_bridge_events(
    bridge: GeminiLiveBridge,
    websocket: WebSocket,
    orch: OrderOrchestrator,
    db: Session,
) -> None:
    """Forward Gemini Live bridge events to the voice WebSocket safely."""
    async for event in bridge.iter_events(continuous=True):
        if isinstance(event, BargeInEvent):
            await websocket.send_json(
                {"type": "barge_in", "interrupted_at_ms": event.interrupted_at_ms}
            )
        elif isinstance(event, ToolCallRequestEvent):
            tool_result = orch.dispatch_tool(event.name, event.args)
            await bridge.send_tool_response(event.call_id, event.name, tool_result)
            order = orch.active_order_id and repo.get_order(db, orch.active_order_id)
            await websocket.send_json(
                {
                    "type": "tool_call",
                    "name": event.name,
                    "result": tool_result,
                    "order_updated": order.model_dump(mode="json") if order else None,
                }
            )
        elif isinstance(event, AudioChunkEvent):
            await websocket.send_json(
                {
                    "type": "audio_out",
                    "pcm_b64": base64.b64encode(event.pcm_bytes).decode("ascii"),
                    "sample_rate": event.sample_rate,
                }
            )
        else:
            try:
                payload = event.model_dump(mode="json")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Skipping voice event %s due to serialization error: %s",
                    getattr(event, "type", type(event).__name__),
                    exc,
                )
                continue
            await websocket.send_json(payload)


@app.websocket("/ws/voice/{session_id}")
async def ws_voice(websocket: WebSocket, session_id: str) -> None:
    """Bidirectional PCM16 audio ↔ Gemini Live bridge with tool dispatch."""
    await websocket.accept()

    settings = get_settings()

    with Session(_engine) as db:
        orch = OrderOrchestrator(
            db,
            session_id=session_id,
            rep_id=f"ws-rep-{session_id[:8]}",
            as_of=date.today(),
            trace_hub=_trace_hub,
            auto_commit=True,
        )
        _sessions[session_id] = orch

        from app.agent import get_gemini_tools

        tools_list = get_gemini_tools()
        tool_dicts = [t.model_dump(exclude_none=True) for t in tools_list]

        bridge = GeminiLiveBridge(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            tools=tool_dicts,  # type: ignore[arg-type]
        )

        try:
            async with bridge:
                # Receive from Gemini and forward to WebSocket
                async def _recv_loop() -> None:
                    await _forward_bridge_events(bridge, websocket, orch, db)

                recv_task = asyncio.create_task(_recv_loop())

                # Forward WebSocket messages to Gemini
                try:
                    while True:
                        message = await websocket.receive()
                        if message.get("type") == "websocket.disconnect":
                            break
                        if message.get("bytes"):
                            await bridge.send_audio_chunk(message["bytes"])
                        elif message.get("text"):
                            data = json.loads(message["text"])
                            msg_type = data.get("type", "")
                            if msg_type == "text_turn":
                                await bridge.send_text_turn(str(data.get("text", "")))
                            elif msg_type == "barge_in":
                                # Client signals barge-in; Gemini VAD handles it
                                pass
                except (WebSocketDisconnect, WebSocketDisconnected):
                    pass
                finally:
                    recv_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await recv_task

        except Exception as exc:
            logger.exception("WebSocket voice error for session %s", session_id)
            with contextlib.suppress(Exception):
                await websocket.send_json({"type": "error", "message": str(exc)})
        finally:
            with contextlib.suppress(Exception):
                await websocket.close()


# Deferred import to satisfy mypy
import contextlib
