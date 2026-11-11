import asyncio
import contextlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

TraceStatus = Literal["OK", "ERROR", "BLOCKED"]


class TraceEntry(BaseModel):
    """Structured telemetry event for one orchestrator tool invocation."""

    event_id: int
    session_id: str
    order_id: str | None = None
    tool_name: str
    input_payload: dict[str, object]
    output_payload: dict[str, object]
    status: TraceStatus
    latency_ms: int
    created_at: datetime


class TraceStreamHub:
    """In-memory trace history and async queue pub/sub hub for live streaming."""

    def __init__(self) -> None:
        self._entries: list[TraceEntry] = []
        self._subscribers: set[asyncio.Queue[TraceEntry]] = set()
        self._next_event_id = 1

    def allocate_event_id(self) -> int:
        """Return the next monotonically increasing trace event ID."""
        event_id = self._next_event_id
        self._next_event_id += 1
        return event_id

    def publish(self, entry: TraceEntry) -> TraceEntry:
        """Append a trace entry to history and notify active subscribers."""
        self._entries.append(entry)
        for queue in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(entry)
        return entry

    def subscribe(self) -> asyncio.Queue[TraceEntry]:
        """Register and return a subscriber queue for live trace updates."""
        queue: asyncio.Queue[TraceEntry] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[TraceEntry]) -> None:
        """Remove a subscriber queue cleanly."""
        self._subscribers.discard(queue)

    def list_entries(self, session_id: str | None = None) -> list[TraceEntry]:
        """Return recorded trace entries, optionally filtered by session_id."""
        if session_id is None:
            return list(self._entries)
        return [entry for entry in self._entries if entry.session_id == session_id]

