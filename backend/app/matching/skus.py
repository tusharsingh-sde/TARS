"""Pure RapidFuzz SKU resolution against ERP SkuDTO records.

Known behaviour: ``rapidfuzz.fuzz.token_set_ratio`` returns ``100.0`` whenever all query
tokens appear as a subset of a candidate's ``brand product`` or alias tokens. Short or
generic queries such as ``"atta"`` therefore score ``1.0`` across multiple SKUs and must
resolve to ``AMBIGUOUS`` rather than ``MATCHED``.
"""

from collections.abc import Sequence
from typing import Final, NamedTuple

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from app.config import get_settings
from app.erp.schemas import SkuDTO
from app.matching.line_parser import ParsedLine
from app.matching.normalize import normalize_text
from app.matching.outlets import CLARIFY_FLOOR, MAX_CANDIDATES, MIN_WINNER_GAP, MatchStatus

MULTIPLE_PACK_SIZES_WARNING: Final[str] = "multiple pack sizes"


class SkuCandidate(NamedTuple):
    """Single scored SKU candidate tuple `(sku_id, label, score)`."""

    sku_id: str
    label: str
    score: float


class SkuMatchResult(BaseModel):
    """Outcome of resolving a parsed order fragment against catalog SKUs."""

    status: MatchStatus
    candidates: list[SkuCandidate] = Field(default_factory=list, max_length=MAX_CANDIDATES)
    best: SkuCandidate | None = None
    needs_pack: bool = False


def _normalize_pack(value: str) -> str:
    """Lowercase a pack size string and strip all whitespace for catalog comparison."""
    return "".join(value.split()).lower()


def _format_sku_label(sku: SkuDTO) -> str:
    """Format human-readable candidate label as 'brand product pack_size'."""
    return f"{sku.brand} {sku.product} {sku.pack_size}"


def _score_sku(normalized_query: str, sku: SkuDTO) -> float:
    """Compute max RapidFuzz token_set_ratio in [0.0, 1.0] across 'brand product' and aliases."""
    brand_product = normalize_text(f"{sku.brand} {sku.product}")
    targets = [brand_product, *(normalize_text(alias) for alias in sku.aliases)]
    non_empty_targets = [target for target in targets if target]
    if not non_empty_targets:
        return 0.0
    return max(
        fuzz.token_set_ratio(normalized_query, target) / 100.0
        for target in non_empty_targets
    )


def _score_catalog(normalized_query: str, skus: Sequence[SkuDTO]) -> list[tuple[SkuDTO, SkuCandidate]]:
    """Score and sort SKUs descending by score, breaking ties deterministically by sku_id."""
    scored = [
        (
            sku,
            SkuCandidate(
                sku_id=sku.sku_id,
                label=_format_sku_label(sku),
                score=_score_sku(normalized_query, sku),
            ),
        )
        for sku in skus
    ]
    scored.sort(key=lambda item: (-item[1].score, item[0].sku_id))
    return scored


def _has_clear_winner(scored: list[tuple[SkuDTO, SkuCandidate]]) -> bool:
    """Return True if only one SKU is scored or top beats runner-up by MIN_WINNER_GAP."""
    if len(scored) < 2:
        return True
    gap = round(scored[0][1].score - scored[1][1].score, 9)
    return gap >= MIN_WINNER_GAP


def _shares_brand_product_with_distinct_packs(
    scored: list[tuple[SkuDTO, SkuCandidate]],
) -> bool:
    """Check if close top contenders share one brand+product but differ in pack_size."""
    if len(scored) < 2:
        return False
    top_score = scored[0][1].score
    close_skus = [
        sku for sku, cand in scored if round(top_score - cand.score, 9) < MIN_WINNER_GAP
    ]
    if len(close_skus) < 2:
        return False
    brand_products = {
        (normalize_text(sku.brand), normalize_text(sku.product))
        for sku in close_skus
    }
    packs = {_normalize_pack(sku.pack_size) for sku in close_skus}
    return len(brand_products) == 1 and len(packs) > 1


def resolve_sku(
    parsed: ParsedLine,
    skus: Sequence[SkuDTO],
    threshold: float | None = None,
) -> SkuMatchResult:
    """Resolve a ParsedLine against catalog SkuDTO records with no database access."""
    query = normalize_text(parsed.product_text)
    if not query or not skus:
        return SkuMatchResult(status="NOT_FOUND", candidates=[], best=None, needs_pack=False)

    filtered_skus: Sequence[SkuDTO]
    if parsed.pack_size is not None:
        target_pack = _normalize_pack(parsed.pack_size)
        filtered_skus = [sku for sku in skus if _normalize_pack(sku.pack_size) == target_pack]
        if not filtered_skus:
            return SkuMatchResult(status="NOT_FOUND", candidates=[], best=None, needs_pack=False)
    else:
        filtered_skus = skus

    effective_threshold = (
        get_settings().sku_match_threshold if threshold is None else threshold
    )

    scored = _score_catalog(query, filtered_skus)
    top_candidates = [cand for _, cand in scored[:MAX_CANDIDATES]]
    top_candidate = top_candidates[0]

    if top_candidate.score < CLARIFY_FLOOR:
        return SkuMatchResult(
            status="NOT_FOUND",
            candidates=top_candidates,
            best=None,
            needs_pack=False,
        )

    has_multiple_packs = MULTIPLE_PACK_SIZES_WARNING in parsed.warnings
    if has_multiple_packs:
        unfiltered_scored = scored if parsed.pack_size is None else _score_catalog(query, skus)
        needs_pack = _shares_brand_product_with_distinct_packs(unfiltered_scored)
        return SkuMatchResult(
            status="AMBIGUOUS",
            candidates=top_candidates,
            best=None,
            needs_pack=needs_pack,
        )

    if top_candidate.score >= effective_threshold and _has_clear_winner(scored):
        return SkuMatchResult(
            status="MATCHED",
            candidates=top_candidates,
            best=top_candidate,
            needs_pack=False,
        )

    needs_pack = (
        parsed.pack_size is None and _shares_brand_product_with_distinct_packs(scored)
    )
    return SkuMatchResult(
        status="AMBIGUOUS",
        candidates=top_candidates,
        best=None,
        needs_pack=needs_pack,
    )
