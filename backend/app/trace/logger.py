import json
from typing import cast

from sqlalchemy.orm import Session

from app.erp import repo
from app.erp.models import utc_now
from app.trace.stream import TraceEntry, TraceStatus, TraceStreamHub


def _to_json_safe(payload: dict[str, object]) -> dict[str, object]:
    serialized = json.dumps(payload, default=str, ensure_ascii=True)
    return cast(dict[str, object], json.loads(serialized))


def record_tool_trace(
    db: Session,
    hub: TraceStreamHub,
    *,
    session_id: str,
    order_id: str | None,
    tool_name: str,
    input_payload: dict[str, object],
    output_payload: dict[str, object],
    status: TraceStatus,
    latency_ms: int,
) -> TraceEntry:
    """Persist a tool audit event in ERP and publish a live TraceEntry."""
    safe_input = _to_json_safe(input_payload)
    safe_output = _to_json_safe(output_payload)
    bounded_latency = max(0, latency_ms)

    repo.append_audit_event(
        db,
        order_id,
        event_type=f"TOOL:{tool_name}",
        payload={
            "session_id": session_id,
            "tool_name": tool_name,
            "input": safe_input,
            "output": safe_output,
            "status": status,
            "latency_ms": bounded_latency,
        },
    )

    entry = TraceEntry(
        event_id=hub.allocate_event_id(),
        session_id=session_id,
        order_id=order_id,
        tool_name=tool_name,
        input_payload=safe_input,
        output_payload=safe_output,
        status=status,
        latency_ms=bounded_latency,
        created_at=utc_now(),
    )
    return hub.publish(entry)

