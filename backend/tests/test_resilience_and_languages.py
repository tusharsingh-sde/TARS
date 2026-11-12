"""Phase 6 tests: Resilience, Multilingual Code-Switching (Hinglish/Tanglish/Marathi), and Exceptions.

Covers:
- P6-01: Synthetic noisy audio clips (.wav 16kHz 16-bit PCM mono), frame validation, and chunking
- P6-02: Second language support (Tamil/Tanglish and Marathi)
- P6-03: Code-switching parsing and full orchestrator workflows
- P6-04: Exception scenarios:
    - Scenario A: Noisy / garbled transcript never guesses
    - Scenario B: Full Tanglish & Marathi code-switched orders
    - Scenario C: Ambiguous SKU missing pack size blocks addition
    - Scenario D: Ambiguous or wrong outlet never starts draft
    - Scenario E: Out-of-stock SKUs blocked with LOW_STOCK and shortfall
"""

from __future__ import annotations

import wave
from collections.abc import Generator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.agent.orchestrator import OrderOrchestrator
from app.erp import repo
from app.erp.models import Base
from app.erp.seed import seed_database
from app.matching.line_parser import (
    MARATHI_NUMBER_WORDS,
    NUMBER_WORDS,
    TANGLISH_NUMBER_WORDS,
    parse_line,
)
from app.matching.normalize import normalize_text
from app.voice.audio import (
    CHANNELS,
    INPUT_SAMPLE_RATE,
    SAMPLE_WIDTH_BYTES,
    chunk_pcm16,
    pcm_duration_ms,
    validate_pcm16_frame,
)
from app.voice.prompts import SYSTEM_INSTRUCTION

# ---------------------------------------------------------------------------
# Database Fixture
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# P6-01: Noisy Audio Clips & PCM Validation
# ---------------------------------------------------------------------------


def test_synthetic_wav_clips_exist_and_validate_cleanly() -> None:
    """Verify that all three 16kHz 16-bit mono synthetic WAV clips validate cleanly."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    recordings_dir = repo_root / "demo" / "recordings"

    clip_names = [
        "noisy_shop_order_16k.wav",
        "clipped_low_snr_16k.wav",
        "barge_in_correction_16k.wav",
    ]

    for name in clip_names:
        wav_path = recordings_dir / name
        assert wav_path.is_file(), f"Expected audio file {wav_path} to exist"

        with wave.open(str(wav_path), "rb") as wf:
            assert wf.getnchannels() == CHANNELS
            assert wf.getsampwidth() == SAMPLE_WIDTH_BYTES
            assert wf.getframerate() == INPUT_SAMPLE_RATE
            n_frames = wf.getnframes()
            assert n_frames == INPUT_SAMPLE_RATE  # 1.0 second duration
            pcm_data = wf.readframes(n_frames)

        # Validate raw PCM frames
        validated = validate_pcm16_frame(pcm_data)
        assert len(validated) == INPUT_SAMPLE_RATE * SAMPLE_WIDTH_BYTES  # 32000 bytes

        # Validate duration
        dur_ms = pcm_duration_ms(validated, sample_rate=INPUT_SAMPLE_RATE)
        assert dur_ms == 1000

        # Validate chunking into 3200-byte frames (100ms chunks)
        chunks = chunk_pcm16(validated, chunk_bytes=3200)
        assert len(chunks) == 10
        for chunk in chunks:
            assert len(chunk) == 3200
            assert len(chunk) % SAMPLE_WIDTH_BYTES == 0


def test_invalid_pcm_frames_raise_value_error() -> None:
    """PCM validator must reject empty or misaligned audio frames."""
    with pytest.raises(ValueError, match="must not be empty"):
        validate_pcm16_frame(b"")

    with pytest.raises(ValueError, match="multiple of 2 bytes"):
        validate_pcm16_frame(b"\x00\x01\x02")  # 3 bytes (odd)


# ---------------------------------------------------------------------------
# P6-02 / P6-03: Multilingual Number & Token Parsing (Tanglish & Marathi)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("onnu", 1),
        ("oru", 1),
        ("rendu", 2),
        ("moonu", 3),
        ("naalu", 4),
        ("anju", 5),
        ("aaru", 6),
        ("ezhu", 7),
        ("ettu", 8),
        ("ombadhu", 9),
        ("onpadhu", 9),
        ("pathu", 10),
    ],
)
def test_tanglish_number_words(word: str, expected: int) -> None:
    assert TANGLISH_NUMBER_WORDS[word] == expected
    assert NUMBER_WORDS[word] == expected
    parsed = parse_line(f"{word} box biscuits")
    assert parsed.quantity == expected
    assert parsed.unit == "carton"


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("don", 2),
        ("paach", 5),
        ("saha", 6),
        ("daha", 10),
    ],
)
def test_marathi_number_words(word: str, expected: int) -> None:
    assert MARATHI_NUMBER_WORDS[word] == expected
    assert NUMBER_WORDS[word] == expected
    parsed = parse_line(f"{word} peti oil")
    assert parsed.quantity == expected
    assert parsed.unit == "carton"


@pytest.mark.parametrize(
    ("utterance", "expected_qty", "expected_unit", "expected_pack", "expected_prod"),
    [
        # Tanglish examples
        ("rendu peti surya glucose 100g kudunga", 2, "carton", "100g", "surya glucose"),
        ("moonu bag chamak 1kg podunga", 3, "bag", "1kg", "chamak"),
        ("naalu piece lemon gel 500ml venum annachi", 4, "piece", "500ml", "lemon gel"),
        ("onnu box swadista noodles 70g kudu", 1, "carton", "70g", "swadista noodles"),
        ("anju packet tea 250g podu", 5, "bag", "250g", "tea"),
        # Marathi examples
        ("don bori aashirvaad 5kg dya", 2, "bag", "5kg", "aashirvaad"),
        ("paach box swadista noodles 70g pathva", 5, "carton", "70g", "swadista noodles"),
        ("daha packet poha 500g taka", 10, "bag", "500g", "poha"),
        ("teen pishvi detergent 2kg pahije", 3, "bag", "2kg", "detergent"),
        ("ek dabba pickle 200g dya", 1, "piece", "200g", "pickle"),
    ],
)
def test_code_switched_utterance_parsing(
    utterance: str,
    expected_qty: int,
    expected_unit: str,
    expected_pack: str,
    expected_prod: str,
) -> None:
    parsed = parse_line(normalize_text(utterance))
    assert parsed.quantity == expected_qty
    assert parsed.unit == expected_unit
    assert parsed.pack_size == expected_pack
    assert parsed.product_text == expected_prod
    assert parsed.warnings == []


def test_system_prompt_supports_tanglish_and_marathi() -> None:
    """Prompt must explicitly instruct the assistant to support Tanglish and Marathi."""
    lowered = SYSTEM_INSTRUCTION.lower()
    assert "tanglish" in lowered
    assert "marathi" in lowered
    assert "hinglish" in lowered
    assert "never guess" in lowered


# ---------------------------------------------------------------------------
# P6-04 Exception Scenario A: Noisy / Incomplete Audio Never Guesses
# ---------------------------------------------------------------------------


def test_scenario_a_noisy_and_garbled_audio_never_guesses(seeded_db: Session) -> None:
    """Garbled or inaudible transcript never mutates order lines or guesses silently."""
    orch = OrderOrchestrator(
        seeded_db,
        session_id="sess-noisy-01",
        rep_id="REP-NOISE-01",
        as_of=date(2026, 1, 1),
    )

    # 1. Resolve valid outlet
    orch.dispatch_tool("resolve_outlet", {"query": "tiwari ji"})
    assert orch.active_order_id is not None
    order_before = repo.get_order(seeded_db, orch.active_order_id)
    assert order_before is not None
    assert len(order_before.lines) == 0

    # 2. Garbled / inaudible transcript: "... [inaudible] ... 5 peti ..."
    resp1 = orch.dispatch_tool(
        "add_or_update_line",
        {"utterance_or_product": "... [inaudible] ... 5 peti ..."},
    )
    assert resp1["status"] in {"NOT_FOUND", "AMBIGUOUS"}
    # Draft order lines must remain completely untouched
    order_after1 = repo.get_order(seeded_db, orch.active_order_id)
    assert order_after1 is not None
    assert len(order_after1.lines) == 0

    # 3. Static / cough / noise transcript
    resp2 = orch.dispatch_tool(
        "add_or_update_line",
        {"utterance_or_product": "[static noise] [cough] khrrrr"},
    )
    assert resp2["status"] in {"NOT_FOUND", "AMBIGUOUS"}
    order_after2 = repo.get_order(seeded_db, orch.active_order_id)
    assert order_after2 is not None
    assert len(order_after2.lines) == 0

    # 4. Missing product (only quantity and unit): "5 cartons"
    resp3 = orch.dispatch_tool(
        "add_or_update_line",
        {"utterance_or_product": "5 cartons"},
    )
    assert resp3["status"] in {"NOT_FOUND", "AMBIGUOUS"}
    order_after3 = repo.get_order(seeded_db, orch.active_order_id)
    assert order_after3 is not None
    assert len(order_after3.lines) == 0


# ---------------------------------------------------------------------------
# P6-04 Exception Scenario B: Multilingual End-to-End Orders (Tanglish & Marathi)
# ---------------------------------------------------------------------------


def test_scenario_b_tanglish_and_marathi_multilingual_orders(seeded_db: Session) -> None:
    """End-to-end order with mixed Tanglish and Marathi line additions, read-back, and commit."""
    orch = OrderOrchestrator(
        seeded_db,
        session_id="sess-multi-01",
        rep_id="REP-MULTI-01",
        as_of=date(2026, 1, 1),
    )

    # 1. Resolve outlet
    out_res = orch.dispatch_tool("resolve_outlet", {"query": "tiwari provision store"})
    assert out_res["status"] == "MATCHED"

    # 2. Add Tanglish line: "rendu peti surya glucose 100g kudunga" (SKU-001, qty=2)
    line1_res = orch.dispatch_tool(
        "add_or_update_line",
        {"utterance_or_product": "rendu peti surya glucose 100g kudunga"},
    )
    assert line1_res["status"] == "OK"
    assert line1_res["sku"]["sku_id"] == "SKU-001"
    assert line1_res["order"]["lines"][0]["quantity"] == 2

    # 3. Add Marathi line: "paach box swadista noodles 70g pathva" (SKU-009, qty=5)
    line2_res = orch.dispatch_tool(
        "add_or_update_line",
        {"utterance_or_product": "paach box swadista noodles 70g pathva"},
    )
    assert line2_res["status"] == "OK"
    assert line2_res["sku"]["sku_id"] == "SKU-009"
    assert line2_res["order"]["lines"][1]["quantity"] == 5

    # 4. Verify draft order has both lines with correct totals
    assert orch.active_order_id is not None
    order = repo.get_order(seeded_db, orch.active_order_id)
    assert order is not None
    assert len(order.lines) == 2
    sku_ids = {l.sku_id for l in order.lines}
    assert sku_ids == {"SKU-001", "SKU-009"}
    assert order.total_paise > 0

    # 5. Spoken read-back
    rb_res = orch.dispatch_tool(
        "record_read_back",
        {"spoken_summary": "Tiwari Store: 2 peti Surya Glucose 100g, 5 box Swadista Noodles 70g"},
    )
    assert rb_res["status"] == "OK"

    # 6. Spoken confirmation commits order
    commit_res = orch.dispatch_tool("commit_order", {"confirmation_phrase": "confirm"})
    assert commit_res["status"] == "OK"
    assert commit_res["order_status"] == "COMMITTED"
    assert commit_res["invoice"] is not None


# ---------------------------------------------------------------------------
# P6-04 Exception Scenario C: Ambiguous SKU & Missing Pack Size
# ---------------------------------------------------------------------------


def test_scenario_c_ambiguous_sku_without_pack_size_blocks_line(seeded_db: Session) -> None:
    """Ordering multi-pack product without specifying pack size returns AMBIGUOUS with needs_pack."""
    orch = OrderOrchestrator(
        seeded_db,
        session_id="sess-ambig-sku-01",
        rep_id="REP-AMBIG-01",
        as_of=date(2026, 1, 1),
    )

    orch.dispatch_tool("resolve_outlet", {"query": "tiwari ji"})
    assert orch.active_order_id is not None

    # Ambiguous: "surya glucose" exists in both 100g (SKU-001) and 50g (SKU-002)
    resp = orch.dispatch_tool(
        "add_or_update_line",
        {"utterance_or_product": "5 cartons surya glucose"},
    )
    assert resp["status"] == "AMBIGUOUS"
    assert resp["needs_pack"] is True
    assert len(resp["candidates"]) >= 2
    cand_labels = [c["label"] for c in resp["candidates"]]
    assert any("100g" in lbl for lbl in cand_labels)
    assert any("50g" in lbl for lbl in cand_labels)

    # Order lines must remain empty
    order = repo.get_order(seeded_db, orch.active_order_id)
    assert order is not None
    assert len(order.lines) == 0

    # Rep clarifies with pack size "100g"
    clarified_resp = orch.dispatch_tool(
        "add_or_update_line",
        {"utterance_or_product": "5 cartons surya glucose 100g"},
    )
    assert clarified_resp["status"] == "OK"
    assert clarified_resp["sku"]["sku_id"] == "SKU-001"

    order_updated = repo.get_order(seeded_db, orch.active_order_id)
    assert order_updated is not None
    assert len(order_updated.lines) == 1


# ---------------------------------------------------------------------------
# P6-04 Exception Scenario D: Wrong or Ambiguous Outlet
# ---------------------------------------------------------------------------


def test_scenario_d_ambiguous_or_wrong_outlet_blocks_draft(seeded_db: Session) -> None:
    """Ambiguous query matching multiple outlets within threshold gap refuses to start draft."""
    orch = OrderOrchestrator(
        seeded_db,
        session_id="sess-ambig-out-01",
        rep_id="REP-OUT-01",
        as_of=date(2026, 1, 1),
    )

    # 1. Ambiguous outlet query: "sharma" matches both OUT-001 and OUT-002
    resp_ambig = orch.dispatch_tool("resolve_outlet", {"query": "sharma"})
    assert resp_ambig["status"] == "AMBIGUOUS"
    assert resp_ambig["order_id"] is None
    assert orch.active_order_id is None
    assert len(resp_ambig["candidates"]) >= 2

    # Attempting to add a line when outlet is ambiguous must be BLOCKED
    line_resp = orch.dispatch_tool(
        "add_or_update_line",
        {"utterance_or_product": "5 cartons bhoomi salt 1kg"},
    )
    assert line_resp["status"] == "BLOCKED"
    assert line_resp["reason"] == "NO_ACTIVE_ORDER"

    # 2. Unknown outlet: completely fictional query with score below CLARIFY_FLOOR
    resp_unknown = orch.dispatch_tool(
        "resolve_outlet",
        {"query": "xyzabcdef qrstuvwxyz 999"},
    )
    assert resp_unknown["status"] == "NOT_FOUND"
    assert resp_unknown["order_id"] is None
    assert orch.active_order_id is None


# ---------------------------------------------------------------------------
# P6-04 Exception Scenario E: Out-of-Stock SKUs Blocked with Shortfall
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("utterance", "expected_sku_id"),
    [
        ("2 bags stain buster 2kg", "SKU-019"),
        ("5 cartons spring mineral water 1L", "SKU-038"),
        ("3 cartons classic salted chips 52g", "SKU-057"),
        ("4 piece dark soy sauce 200g", "SKU-076"),
    ],
)
def test_scenario_e_out_of_stock_skus_blocked_with_shortfall(
    seeded_db: Session,
    utterance: str,
    expected_sku_id: str,
) -> None:
    """Ordering any of the 4 zero-stock SKUs returns BLOCKED with LOW_STOCK and shortfall."""
    orch = OrderOrchestrator(
        seeded_db,
        session_id=f"sess-stock-{expected_sku_id}",
        rep_id="REP-STOCK-01",
        as_of=date(2026, 1, 1),
    )

    # 1. Resolve outlet
    orch.dispatch_tool("resolve_outlet", {"query": "tiwari ji"})
    assert orch.active_order_id is not None

    # Verify catalog has 0 stock for this SKU
    sku = repo.get_sku(seeded_db, expected_sku_id)
    assert sku is not None
    assert sku.stock_units == 0

    # 2. Attempt to add zero-stock SKU
    resp = orch.dispatch_tool("add_or_update_line", {"utterance_or_product": utterance})
    assert resp["status"] == "BLOCKED"
    assert resp["reason"] == "LOW_STOCK"
    assert resp["shortfall"] > 0
    assert resp["sku"]["sku_id"] == expected_sku_id

    # 3. Verify order lines remain completely empty
    order = repo.get_order(seeded_db, orch.active_order_id)
    assert order is not None
    assert len(order.lines) == 0
