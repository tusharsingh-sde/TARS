from collections.abc import Sequence
from datetime import date
from typing import Final

from pydantic import BaseModel

from app.erp.schemas import SchemeDTO, SkuDTO

MAX_EXTRA_DISCOUNT_BPS: Final[int] = 200
NEAR_MISS_NUMERATOR: Final[int] = 3
NEAR_MISS_DENOMINATOR: Final[int] = 4
BPS_PER_PERCENT: Final[int] = 100


class DiscountEvaluation(BaseModel):
    """Outcome of active scheme lookup, upsell hint, and extra-discount policy check."""

    applied_discount_bps: int = 0
    upsell_hint: str | None = None
    policy_breach: str | None = None


def _format_bps_percent(discount_bps: int) -> str:
    whole, remainder = divmod(discount_bps, BPS_PER_PERCENT)
    if remainder == 0:
        return f"{whole}%"
    decimal = f"{remainder:02d}".rstrip("0")
    return f"{whole}.{decimal}%"


def _build_upsell_hint(
    sku: SkuDTO,
    requested_qty: int,
    best_scheme_bps: int,
    near_miss_schemes: Sequence[SchemeDTO],
) -> str | None:
    upgrades = [s for s in near_miss_schemes if s.discount_bps > best_scheme_bps]
    if not upgrades:
        return None
    best_upgrade = max(upgrades, key=lambda s: (s.discount_bps, -s.min_qty))
    shortfall = best_upgrade.min_qty - requested_qty
    unit_label = sku.unit if shortfall == 1 else f"{sku.unit}s"
    pct_label = _format_bps_percent(best_upgrade.discount_bps)
    return f"Add {shortfall} more {unit_label} to unlock {pct_label} scheme discount"


def evaluate_discount(
    sku: SkuDTO,
    requested_qty: int,
    schemes: Sequence[SchemeDTO],
    as_of: date,
    extra_discount_bps: int = 0,
) -> DiscountEvaluation:
    """Evaluate active SKU schemes as of a given date and check the extra-discount cap."""
    if requested_qty <= 0:
        raise ValueError("requested_qty must be positive")
    if extra_discount_bps < 0:
        raise ValueError("extra_discount_bps must be non-negative")

    best_scheme_bps = 0
    near_miss_schemes: list[SchemeDTO] = []

    for scheme in schemes:
        if scheme.sku_id != sku.sku_id:
            continue
        if not (scheme.valid_from <= as_of <= scheme.valid_to):
            continue
        if scheme.min_qty <= 0:
            raise ValueError("scheme.min_qty must be positive")
        if scheme.discount_bps < 0:
            raise ValueError("scheme.discount_bps must be non-negative")

        if requested_qty >= scheme.min_qty:
            best_scheme_bps = max(best_scheme_bps, scheme.discount_bps)
        elif NEAR_MISS_DENOMINATOR * requested_qty >= NEAR_MISS_NUMERATOR * scheme.min_qty:
            near_miss_schemes.append(scheme)

    upsell_hint = _build_upsell_hint(sku, requested_qty, best_scheme_bps, near_miss_schemes)
    policy_breach = (
        "DISCOUNT_ABOVE_POLICY" if extra_discount_bps > MAX_EXTRA_DISCOUNT_BPS else None
    )

    return DiscountEvaluation(
        applied_discount_bps=best_scheme_bps + extra_discount_bps,
        upsell_hint=upsell_hint,
        policy_breach=policy_breach,
    )
