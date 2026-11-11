"""Test automated 3-minute hackathon demo double rehearsal via pytest."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from scripts.rehearse_demo import run_single_rehearsal, setup_rehearsal_engine


def test_double_demo_rehearsal_end_to_end() -> None:
    """Verify that both Rehearsal Run #1 and Run #2 execute with zero failures and identical state."""
    engine = setup_rehearsal_engine()
    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            res1 = run_single_rehearsal(client, 1)
            assert res1["invoice_id"] == "INV-000001"
            assert res1["trace_count"] >= 8
            assert res1["total_elapsed_ms"] > 0
            assert res1["median_tool_latency_ms"] >= 0

            # Second run verifies clean reset and repeatability
            res2 = run_single_rehearsal(client, 2)
            assert res2["invoice_id"] == "INV-000001"
            assert res2["trace_count"] >= 8
            assert res2["total_elapsed_ms"] > 0
            assert res2["median_tool_latency_ms"] >= 0
    finally:
        engine.dispose()

