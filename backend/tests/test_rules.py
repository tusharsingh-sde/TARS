from datetime import date

import pytest

from app.erp.schemas import OutletDTO, SchemeDTO, SkuDTO
from app.rules import (
    MAX_EXTRA_DISCOUNT_BPS,
    CreditEvaluation,
    DiscountEvaluation,
    StockEvaluation,
    evaluate_credit,
    evaluate_discount,
    evaluate_stock,
)


def _make_outlet(
    *,
    credit_limit_paise: int = 500_000,
    outstanding_paise: int = 200_000,
) -> OutletDTO:
    return OutletDTO(
        outlet_id="OUT-001",
        name="Sharma Kirana Store",
        owner_name="Ramesh Sharma",
        locality="Lajpat Nagar",
        city="Delhi",
        route_code="DEL-S1",
        credit_limit_paise=credit_limit_paise,
        outstanding_paise=outstanding_paise,
        aliases=["sharma ji", "sharma kirana"],
    )


def _make_sku(
    *,
    sku_id: str = "SKU-001",
    unit: str = "carton",
    stock_units: int = 20,
) -> SkuDTO:
    return SkuDTO(
        sku_id=sku_id,
        brand="Tata",
        product="Tata Salt",
        pack_size="1kg",
        unit=unit,
        mrp_paise=28_000,
        base_price_paise=24_000,
        stock_units=stock_units,
        aliases=["tata namak"],
    )


def _make_scheme(
    *,
    scheme_id: str = "SCH-001",
    sku_id: str = "SKU-001",
    min_qty: int = 10,
    discount_bps: int = 300,
    valid_from: date = date(2026, 10, 1),
    valid_to: date = date(2026, 12, 31),
) -> SchemeDTO:
    return SchemeDTO(
        scheme_id=scheme_id,
        sku_id=sku_id,
        min_qty=min_qty,
        discount_bps=discount_bps,
        valid_from=valid_from,
        valid_to=valid_to,
        description=f"{discount_bps} bps on {min_qty}+",
    )


def test_credit_approved_below_limit() -> None:
    outlet = _make_outlet(credit_limit_paise=500_000, outstanding_paise=200_000)
    result = evaluate_credit(outlet, 250_000)
    assert isinstance(result, CreditEvaluation)
    assert result.is_approved is True
    assert result.reason is None
    assert result.excess_amount_paise == 0


def test_credit_approved_at_exact_limit() -> None:
    outlet = _make_outlet(credit_limit_paise=500_000, outstanding_paise=200_000)
    result = evaluate_credit(outlet, 300_000)
    assert result.is_approved is True
    assert result.reason is None
    assert result.excess_amount_paise == 0


def test_credit_approved_zero_order_at_limit() -> None:
    outlet = _make_outlet(credit_limit_paise=500_000, outstanding_paise=500_000)
    result = evaluate_credit(outlet, 0)
    assert result.is_approved is True
    assert result.excess_amount_paise == 0


def test_credit_rejected_one_paisa_over_limit() -> None:
    outlet = _make_outlet(credit_limit_paise=500_000, outstanding_paise=200_000)
    result = evaluate_credit(outlet, 300_001)
    assert result.is_approved is False
    assert result.reason == "CREDIT_LIMIT_EXCEEDED"
    assert result.excess_amount_paise == 1


@pytest.mark.parametrize(
    ("credit_limit", "outstanding", "order_total"),
    [
        (500_000, 200_000, -1),
        (-1, 0, 100),
        (500_000, -1, 100),
    ],
)
def test_credit_rejects_negative_inputs(
    credit_limit: int,
    outstanding: int,
    order_total: int,
) -> None:
    outlet = _make_outlet(credit_limit_paise=credit_limit, outstanding_paise=outstanding)
    with pytest.raises(ValueError):
        evaluate_credit(outlet, order_total)


def test_discount_no_matching_scheme() -> None:
    sku = _make_sku(sku_id="SKU-001")
    other_scheme = _make_scheme(sku_id="SKU-999", min_qty=1, discount_bps=500)
    result = evaluate_discount(sku, 5, [other_scheme], as_of=date(2026, 11, 11))
    assert isinstance(result, DiscountEvaluation)
    assert result.applied_discount_bps == 0
    assert result.upsell_hint is None
    assert result.policy_breach is None


def test_discount_active_scheme_exact_min_qty_and_date_boundaries() -> None:
    sku = _make_sku()
    scheme = _make_scheme(
        min_qty=10,
        discount_bps=300,
        valid_from=date(2026, 10, 1),
        valid_to=date(2026, 10, 31),
    )

    on_start = evaluate_discount(sku, 10, [scheme], as_of=date(2026, 10, 1))
    on_end = evaluate_discount(sku, 10, [scheme], as_of=date(2026, 10, 31))
    day_before = evaluate_discount(sku, 10, [scheme], as_of=date(2026, 9, 30))
    day_after = evaluate_discount(sku, 10, [scheme], as_of=date(2026, 11, 1))

    assert on_start.applied_discount_bps == 300
    assert on_end.applied_discount_bps == 300
    assert day_before.applied_discount_bps == 0
    assert day_after.applied_discount_bps == 0


def test_discount_picks_highest_active_tier() -> None:
    sku = _make_sku()
    schemes = [
        _make_scheme(scheme_id="SCH-1", min_qty=5, discount_bps=150),
        _make_scheme(scheme_id="SCH-2", min_qty=10, discount_bps=400),
        _make_scheme(
            scheme_id="SCH-EXPIRED",
            min_qty=1,
            discount_bps=900,
            valid_from=date(2026, 8, 1),
            valid_to=date(2026, 9, 30),
        ),
    ]
    result = evaluate_discount(sku, 10, schemes, as_of=date(2026, 11, 11))
    assert result.applied_discount_bps == 400
    assert result.upsell_hint is None


def test_discount_near_miss_exact_integer_boundary() -> None:
    sku = _make_sku(unit="carton")
    scheme = _make_scheme(min_qty=8, discount_bps=250)

    below_threshold = evaluate_discount(sku, 5, [scheme], as_of=date(2026, 11, 11))
    exact_threshold = evaluate_discount(sku, 6, [scheme], as_of=date(2026, 11, 11))

    assert below_threshold.applied_discount_bps == 0
    assert below_threshold.upsell_hint is None

    assert exact_threshold.applied_discount_bps == 0
    assert exact_threshold.upsell_hint == "Add 2 more cartons to unlock 2.5% scheme discount"


def test_discount_near_miss_non_divisible_integer_threshold_and_singular_unit() -> None:
    sku = _make_sku(unit="bag")
    scheme_four = _make_scheme(min_qty=4, discount_bps=300)
    result_one_short = evaluate_discount(sku, 3, [scheme_four], as_of=date(2026, 11, 11))
    assert result_one_short.upsell_hint == "Add 1 more bag to unlock 3% scheme discount"

    scheme_ten = _make_scheme(min_qty=10, discount_bps=150)
    assert evaluate_discount(sku, 7, [scheme_ten], as_of=date(2026, 11, 11)).upsell_hint is None
    assert (
        evaluate_discount(sku, 8, [scheme_ten], as_of=date(2026, 11, 11)).upsell_hint
        == "Add 2 more bags to unlock 1.5% scheme discount"
    )


def test_discount_ignores_near_miss_with_lower_or_equal_discount() -> None:
    sku = _make_sku()
    schemes = [
        _make_scheme(scheme_id="SCH-LOW", min_qty=5, discount_bps=400),
        _make_scheme(scheme_id="SCH-WORSE", min_qty=8, discount_bps=300),
    ]
    result = evaluate_discount(sku, 6, schemes, as_of=date(2026, 11, 11))
    assert result.applied_discount_bps == 400
    assert result.upsell_hint is None


def test_discount_upsells_to_higher_near_miss_tier() -> None:
    sku = _make_sku()
    schemes = [
        _make_scheme(scheme_id="SCH-1", min_qty=5, discount_bps=200),
        _make_scheme(scheme_id="SCH-2", min_qty=8, discount_bps=500),
    ]
    result = evaluate_discount(sku, 6, schemes, as_of=date(2026, 11, 11))
    assert result.applied_discount_bps == 200
    assert result.upsell_hint == "Add 2 more cartons to unlock 5% scheme discount"


def test_discount_extra_discount_policy_boundary() -> None:
    sku = _make_sku()
    scheme = _make_scheme(min_qty=5, discount_bps=150)

    at_cap = evaluate_discount(
        sku,
        5,
        [scheme],
        as_of=date(2026, 11, 11),
        extra_discount_bps=MAX_EXTRA_DISCOUNT_BPS,
    )
    over_cap = evaluate_discount(
        sku,
        5,
        [scheme],
        as_of=date(2026, 11, 11),
        extra_discount_bps=MAX_EXTRA_DISCOUNT_BPS + 1,
    )

    assert at_cap.applied_discount_bps == 150 + MAX_EXTRA_DISCOUNT_BPS
    assert at_cap.policy_breach is None

    assert over_cap.applied_discount_bps == 150 + MAX_EXTRA_DISCOUNT_BPS + 1
    assert over_cap.policy_breach == "DISCOUNT_ABOVE_POLICY"


@pytest.mark.parametrize(
    ("requested_qty", "extra_discount_bps", "min_qty", "discount_bps"),
    [
        (0, 0, 5, 100),
        (-2, 0, 5, 100),
        (5, -1, 5, 100),
        (5, 0, 0, 100),
        (5, 0, 5, -10),
    ],
)
def test_discount_rejects_invalid_inputs(
    requested_qty: int,
    extra_discount_bps: int,
    min_qty: int,
    discount_bps: int,
) -> None:
    sku = _make_sku()
    scheme = _make_scheme(min_qty=min_qty, discount_bps=discount_bps)
    with pytest.raises(ValueError):
        evaluate_discount(
            sku,
            requested_qty,
            [scheme],
            as_of=date(2026, 11, 11),
            extra_discount_bps=extra_discount_bps,
        )


def test_stock_available_below_and_at_exact_limit() -> None:
    sku = _make_sku(stock_units=10)

    below = evaluate_stock(sku, requested_packs=6, free_qty_packs=2)
    exact = evaluate_stock(sku, requested_packs=8, free_qty_packs=2)

    assert isinstance(below, StockEvaluation)
    assert below.is_available is True
    assert below.reason is None
    assert below.shortfall == 0

    assert exact.is_available is True
    assert exact.reason is None
    assert exact.shortfall == 0


def test_stock_rejected_one_pack_over_available() -> None:
    sku = _make_sku(stock_units=10)

    over_paid = evaluate_stock(sku, requested_packs=11)
    over_with_free = evaluate_stock(sku, requested_packs=8, free_qty_packs=3)
    zero_stock = evaluate_stock(_make_sku(stock_units=0), requested_packs=1)

    assert over_paid.is_available is False
    assert over_paid.reason == "LOW_STOCK"
    assert over_paid.shortfall == 1

    assert over_with_free.is_available is False
    assert over_with_free.reason == "LOW_STOCK"
    assert over_with_free.shortfall == 1

    assert zero_stock.is_available is False
    assert zero_stock.reason == "LOW_STOCK"
    assert zero_stock.shortfall == 1


@pytest.mark.parametrize(
    ("stock_units", "requested_packs", "free_qty_packs"),
    [
        (10, 0, 0),
        (10, -1, 0),
        (10, 2, -1),
        (-1, 2, 0),
    ],
)
def test_stock_rejects_invalid_inputs(
    stock_units: int,
    requested_packs: int,
    free_qty_packs: int,
) -> None:
    sku = _make_sku(stock_units=stock_units)
    with pytest.raises(ValueError):
        evaluate_stock(sku, requested_packs=requested_packs, free_qty_packs=free_qty_packs)

