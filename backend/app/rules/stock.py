from pydantic import BaseModel

from app.erp.schemas import SkuDTO


class StockEvaluation(BaseModel):
    """Outcome of checking whether SKU stock covers requested and free packs."""

    is_available: bool
    reason: str | None = None
    shortfall: int = 0


def evaluate_stock(
    sku: SkuDTO,
    requested_packs: int,
    free_qty_packs: int = 0,
) -> StockEvaluation:
    """Verify SKU stock covers requested packs plus any promotional free packs."""
    if requested_packs <= 0:
        raise ValueError("requested_packs must be positive")
    if free_qty_packs < 0:
        raise ValueError("free_qty_packs must be non-negative")
    if sku.stock_units < 0:
        raise ValueError("sku.stock_units must be non-negative")

    total_needed = requested_packs + free_qty_packs
    if total_needed > sku.stock_units:
        return StockEvaluation(
            is_available=False,
            reason="LOW_STOCK",
            shortfall=total_needed - sku.stock_units,
        )

    return StockEvaluation(is_available=True)
