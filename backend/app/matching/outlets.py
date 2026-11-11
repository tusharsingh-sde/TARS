"""Pure RapidFuzz outlet resolution against ERP OutletDTO records."""

from collections.abc import Sequence
from typing import Final, Literal, NamedTuple

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from app.config import get_settings
from app.erp.schemas import OutletDTO
from app.matching.normalize import normalize_text

CLARIFY_FLOOR: Final[float] = 0.60
MIN_WINNER_GAP: Final[float] = 0.05
MAX_CANDIDATES: Final[int] = 3

MatchStatus = Literal["MATCHED", "AMBIGUOUS", "NOT_FOUND"]


class OutletCandidate(NamedTuple):
    """Single scored outlet candidate tuple `(outlet_id, name, score)`."""

    outlet_id: str
    name: str
    score: float


class MatchResult(BaseModel):
    """Outcome of resolving a spoken outlet name against candidate outlets."""

    status: MatchStatus
    candidates: list[OutletCandidate] = Field(default_factory=list, max_length=MAX_CANDIDATES)
    best: OutletCandidate | None = None


def _score_outlet(normalized_query: str, outlet: OutletDTO) -> float:
    """Compute max RapidFuzz token_set_ratio in [0.0, 1.0] across name and aliases."""
    targets = [normalize_text(outlet.name), *(normalize_text(alias) for alias in outlet.aliases)]
    non_empty_targets = [target for target in targets if target]
    if not non_empty_targets:
        return 0.0
    return max(
        fuzz.token_set_ratio(normalized_query, target) / 100.0
        for target in non_empty_targets
    )


def _has_clear_winner(scored: list[OutletCandidate]) -> bool:
    """Return True if the top candidate beats the runner-up by at least MIN_WINNER_GAP."""
    if len(scored) < 2:
        return True
    # Round before comparing so exact 0.05 decimal differences (e.g. 0.85 - 0.80) hold.
    gap = round(scored[0].score - scored[1].score, 9)
    return gap >= MIN_WINNER_GAP


def resolve_outlet(
    spoken_name: str,
    outlets: Sequence[OutletDTO],
    threshold: float | None = None,
) -> MatchResult:
    """Resolve a spoken outlet name against OutletDTO records without DB I/O."""
    query = normalize_text(spoken_name)
    if not query or not outlets:
        return MatchResult(status="NOT_FOUND", candidates=[], best=None)

    effective_threshold = (
        get_settings().outlet_match_threshold if threshold is None else threshold
    )

    scored = [
        OutletCandidate(
            outlet_id=outlet.outlet_id,
            name=outlet.name,
            score=_score_outlet(query, outlet),
        )
        for outlet in outlets
    ]
    scored.sort(key=lambda item: (-item.score, item.outlet_id))
    top_candidates = scored[:MAX_CANDIDATES]
    top = top_candidates[0]

    if top.score < CLARIFY_FLOOR:
        return MatchResult(status="NOT_FOUND", candidates=top_candidates, best=None)

    if top.score >= effective_threshold and _has_clear_winner(scored):
        return MatchResult(status="MATCHED", candidates=top_candidates, best=top)

    return MatchResult(status="AMBIGUOUS", candidates=top_candidates, best=None)

