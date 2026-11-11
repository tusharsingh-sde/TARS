"""Tests for pure RapidFuzz outlet resolution and text normalization."""

import pytest

from app.config import get_settings
from app.erp.schemas import OutletDTO
from app.matching.normalize import normalize_text
from app.matching.outlets import CLARIFY_FLOOR, resolve_outlet


def _make_outlet(
    outlet_id: str,
    name: str,
    aliases: list[str],
    city: str = "Meerut",
) -> OutletDTO:
    return OutletDTO(
        outlet_id=outlet_id,
        name=name,
        owner_name="Test Owner",
        locality="Market Road",
        city=city,
        route_code="R1",
        credit_limit_paise=5_000_000,
        outstanding_paise=1_000_000,
        aliases=aliases,
    )


@pytest.fixture
def hand_built_outlets() -> list[OutletDTO]:
    """Five hand-built outlets with Hinglish aliases."""
    return [
        _make_outlet(
            "OUT-001",
            "Sharma General Store",
            ["sharma ji", "sharma general", "rameshwar sharma dukan"],
        ),
        _make_outlet(
            "OUT-002",
            "Gupta Kirana Bhandar",
            ["gupta kirana", "gupta ji", "gupta bhandar"],
        ),
        _make_outlet(
            "OUT-003",
            "Verma Provision Mart",
            ["verma mart", "verma provision", "sector 14 verma"],
        ),
        _make_outlet(
            "OUT-004",
            "Patel Super Bazaar",
            ["patel super", "patel bazaar", "patel metro"],
        ),
        _make_outlet(
            "OUT-005",
            "Patel Super Store",
            ["patel super", "patel general", "patel town"],
        ),
    ]


def test_normalize_text_lowercases_and_normalizes_whitespace() -> None:
    assert normalize_text("  Sharma   JI \t Store \n ") == "sharma ji store"
    assert normalize_text("   \t\n  ") == ""


def test_exact_alias_match_returns_matched(hand_built_outlets: list[OutletDTO]) -> None:
    query = normalize_text("Gupta Kirana")
    result = resolve_outlet(query, hand_built_outlets, threshold=0.85)

    assert result.status == "MATCHED"
    assert result.best == ("OUT-002", "Gupta Kirana Bhandar", 1.0)
    assert result.candidates[0] == result.best


def test_sharma_ji_resolves_to_sharma_outlet(hand_built_outlets: list[OutletDTO]) -> None:
    result = resolve_outlet(normalize_text("  Sharma   Ji "), hand_built_outlets, threshold=0.85)

    assert result.status == "MATCHED"
    assert result.best is not None
    assert result.best.outlet_id == "OUT-001"
    assert result.best.name == "Sharma General Store"
    assert result.best.score == pytest.approx(1.0)


def test_two_near_identical_outlets_return_ambiguous(
    hand_built_outlets: list[OutletDTO],
) -> None:
    result = resolve_outlet(normalize_text("patel super"), hand_built_outlets, threshold=0.85)

    assert result.status == "AMBIGUOUS"
    assert result.best is None
    top_two_ids = {candidate.outlet_id for candidate in result.candidates[:2]}
    assert top_two_ids == {"OUT-004", "OUT-005"}


def test_gibberish_returns_not_found(hand_built_outlets: list[OutletDTO]) -> None:
    result = resolve_outlet(normalize_text("zzzz qqqq xxxx"), hand_built_outlets, threshold=0.85)

    assert result.status == "NOT_FOUND"
    assert result.best is None
    assert all(candidate.score < CLARIFY_FLOOR for candidate in result.candidates)


def test_empty_and_whitespace_strings_return_not_found(
    hand_built_outlets: list[OutletDTO],
) -> None:
    for raw in ("", "   \t\n "):
        result = resolve_outlet(normalize_text(raw), hand_built_outlets, threshold=0.85)
        assert result.status == "NOT_FOUND"
        assert result.candidates == []
        assert result.best is None


def test_score_exactly_at_threshold_returns_matched_when_gap_holds() -> None:
    query = "abcdefghijklmnopqxyz"
    # 17/20 matched -> token_set_ratio = 85.0 (0.85); 16/20 matched -> 80.0 (0.80); gap = 0.05.
    constructed_outlets = [
        _make_outlet("OUT-A", "abcdefghijklmnopq123", []),
        _make_outlet("OUT-B", "abcdefghijklmnop1234", []),
        _make_outlet("OUT-C", "abcdefghijklmno12345", []),
    ]

    result = resolve_outlet(query, constructed_outlets, threshold=0.85)

    assert result.status == "MATCHED"
    assert result.best == ("OUT-A", "abcdefghijklmnopq123", 0.85)
    assert [c.score for c in result.candidates] == [0.85, 0.80, 0.75]


def test_score_gap_under_005_returns_ambiguous_even_above_threshold() -> None:
    query = "abcdefghijklmnopqrsx"
    # 18/20 matches -> 0.90 vs 36/41 -> ~0.878 (both >= 0.85, gap ~0.022 < 0.05).
    constructed_outlets = [
        _make_outlet("OUT-A", "abcdefghijklmnopqr12", []),
        _make_outlet("OUT-B", "abcdefghijklmnopqr123", []),
    ]

    result = resolve_outlet(query, constructed_outlets, threshold=0.85)

    assert result.status == "AMBIGUOUS"
    assert result.best is None
    assert len(result.candidates) == 2
    assert result.candidates[0].score == pytest.approx(0.90)
    assert result.candidates[1].score > 0.85


def test_score_between_clarify_floor_and_threshold_returns_ambiguous() -> None:
    query = "abcdefghijklmnopqxyz"
    # 15/20 matched -> 0.75 (between CLARIFY_FLOOR=0.60 and threshold=0.85)
    constructed_outlets = [
        _make_outlet("OUT-MID", "abcdefghijklmno12345", []),
        _make_outlet("OUT-LOW", "abcdefghijk123456789", []),
    ]

    result = resolve_outlet(query, constructed_outlets, threshold=0.85)

    assert result.status == "AMBIGUOUS"
    assert result.best is None
    assert result.candidates[0].score == pytest.approx(0.75)

    at_floor = resolve_outlet(
        query,
        [_make_outlet("OUT-FLOOR", "abcdefghijkl12345678", [])],
        threshold=0.85,
    )
    assert at_floor.status == "AMBIGUOUS"
    assert at_floor.candidates[0].score == pytest.approx(CLARIFY_FLOOR)


def test_candidates_never_exceed_three(hand_built_outlets: list[OutletDTO]) -> None:
    result = resolve_outlet("store", hand_built_outlets, threshold=0.85)

    assert len(result.candidates) == 3
    scores = [candidate.score for candidate in result.candidates]
    assert scores == sorted(scores, reverse=True)


def test_threshold_reads_from_get_settings_when_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = "abcdefghijklmnopqxyz"
    outlets = [
        _make_outlet("OUT-A", "abcdefghijklmnopq123", []),
        _make_outlet("OUT-B", "abcdefghijkl12345678", []),
    ]
    monkeypatch.setenv("TARS_OUTLET_MATCH_THRESHOLD", "0.90")
    get_settings.cache_clear()
    try:
        result = resolve_outlet(query, outlets)
        assert result.status == "AMBIGUOUS"
        assert result.best is None
    finally:
        get_settings.cache_clear()
