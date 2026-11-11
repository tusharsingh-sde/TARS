"""Pure RapidFuzz entity resolution helpers."""

from app.matching.normalize import normalize_text
from app.matching.outlets import (
    CLARIFY_FLOOR,
    MIN_WINNER_GAP,
    MatchResult,
    MatchStatus,
    OutletCandidate,
    resolve_outlet,
)

__all__ = [
    "CLARIFY_FLOOR",
    "MIN_WINNER_GAP",
    "MatchResult",
    "MatchStatus",
    "OutletCandidate",
    "normalize_text",
    "resolve_outlet",
]

