"""Tests for pure RapidFuzz SKU catalog resolution."""

import pytest

from app.config import get_settings
from app.erp.schemas import SkuDTO
from app.matching.line_parser import ParsedLine, parse_line
from app.matching.skus import resolve_sku


def _make_sku(
    sku_id: str,
    brand: str,
    product: str,
    pack_size: str,
    aliases: list[str],
    unit: str = "bag",
) -> SkuDTO:
    return SkuDTO(
        sku_id=sku_id,
        brand=brand,
        product=product,
        pack_size=pack_size,
        unit=unit,
        mrp_paise=10000,
        base_price_paise=8500,
        stock_units=50,
        aliases=aliases,
    )


@pytest.fixture
def sample_skus() -> list[SkuDTO]:
    """Hand-built 12-SKU catalog across multiple brands with multi-pack variants."""
    return [
        _make_sku(
            "SKU-01",
            "Tata",
            "Salt",
            "1kg",
            ["tata namak 1kg", "iodized salt 1kg"],
        ),
        _make_sku(
            "SKU-02",
            "Tata",
            "Salt",
            "500g",
            ["tata namak 500g", "chota tata salt"],
        ),
        _make_sku(
            "SKU-03",
            "Tata",
            "Sampann Poha",
            "500g",
            ["sampann poha", "tata poha"],
        ),
        _make_sku(
            "SKU-04",
            "Aashirvaad",
            "Shudh Atta",
            "5kg",
            ["aashirvaad atta", "aashirvaad chakki atta"],
        ),
        _make_sku(
            "SKU-05",
            "Fortune",
            "Chakki Fresh Atta",
            "10kg",
            ["fortune atta", "chakki atta"],
        ),
        _make_sku(
            "SKU-06",
            "Pillsbury",
            "Gold Chakki Atta",
            "5kg",
            ["pillsbury atta", "gold atta"],
        ),
        _make_sku(
            "SKU-07",
            "Fortune",
            "Mustard Oil",
            "1L",
            ["sarson tel 1L", "fortune tel 1l"],
            unit="carton",
        ),
        _make_sku(
            "SKU-08",
            "Fortune",
            "Sunflower Oil",
            "1L",
            ["refined oil 1L", "sunflower tel"],
            unit="carton",
        ),
        _make_sku(
            "SKU-09",
            "Parle",
            "Gold Biscuit",
            "100g",
            ["parle g gold", "parle biscuit 100g"],
            unit="carton",
        ),
        _make_sku(
            "SKU-10",
            "Parle",
            "Monaco Biscuit",
            "200g",
            ["monaco", "namkeen biscuit"],
            unit="carton",
        ),
        _make_sku(
            "SKU-11",
            "Parle",
            "Krackjack",
            "200g",
            ["krackjack", "parle krackjack"],
            unit="carton",
        ),
        _make_sku(
            "SKU-12",
            "Amul",
            "Pure Ghee",
            "500ml",
            ["amul ghee", "desi ghee 500ml"],
            unit="piece",
        ),
    ]


def test_tata_salt_with_1kg_pack_matched(sample_skus: list[SkuDTO]) -> None:
    parsed = ParsedLine(product_text="tata salt", pack_size="1kg", quantity=5, unit="bag")
    result = resolve_sku(parsed, sample_skus, threshold=0.80)

    assert result.status == "MATCHED"
    assert result.best == ("SKU-01", "Tata Salt 1kg", 1.0)
    assert result.needs_pack is False


def test_tata_salt_without_pack_ambiguous_and_needs_pack(sample_skus: list[SkuDTO]) -> None:
    parsed = ParsedLine(product_text="tata salt", pack_size=None, quantity=2, unit="bag")
    result = resolve_sku(parsed, sample_skus, threshold=0.80)

    assert result.status == "AMBIGUOUS"
    assert result.best is None
    assert result.needs_pack is True
    assert {c.sku_id for c in result.candidates[:2]} == {"SKU-01", "SKU-02"}


def test_aashirvaad_atta_unique_matched(sample_skus: list[SkuDTO]) -> None:
    parsed = ParsedLine(product_text="aashirvaad atta", pack_size=None, quantity=3, unit="bag")
    result = resolve_sku(parsed, sample_skus, threshold=0.80)

    assert result.status == "MATCHED"
    assert result.best is not None
    assert result.best.sku_id == "SKU-04"
    assert result.best.label == "Aashirvaad Shudh Atta 5kg"
    assert result.needs_pack is False


def test_short_query_atta_resolves_to_ambiguous(sample_skus: list[SkuDTO]) -> None:
    parsed = ParsedLine(product_text="atta", pack_size=None, quantity=2, unit="bag")
    result = resolve_sku(parsed, sample_skus, threshold=0.80)

    assert result.status == "AMBIGUOUS"
    assert result.best is None
    assert result.needs_pack is False
    assert {c.sku_id for c in result.candidates} == {"SKU-04", "SKU-05", "SKU-06"}


def test_unknown_product_returns_not_found(sample_skus: list[SkuDTO]) -> None:
    parsed = ParsedLine(product_text="xyzqwerty unknown item", quantity=1)
    result = resolve_sku(parsed, sample_skus, threshold=0.80)

    assert result.status == "NOT_FOUND"
    assert result.best is None
    assert result.needs_pack is False


def test_empty_product_text_returns_not_found(sample_skus: list[SkuDTO]) -> None:
    for product_text in ("", "   \t\n  "):
        parsed = ParsedLine(product_text=product_text, quantity=1)
        result = resolve_sku(parsed, sample_skus, threshold=0.80)

        assert result.status == "NOT_FOUND"
        assert result.candidates == []
        assert result.best is None
        assert result.needs_pack is False


def test_pack_filter_removes_all_candidates_returns_not_found(
    sample_skus: list[SkuDTO],
) -> None:
    parsed = ParsedLine(product_text="tata salt", pack_size="25kg", quantity=1)
    result = resolve_sku(parsed, sample_skus, threshold=0.80)

    assert result.status == "NOT_FOUND"
    assert result.candidates == []
    assert result.best is None
    assert result.needs_pack is False


def test_pack_1l_matches_catalog_1L(sample_skus: list[SkuDTO]) -> None:
    parsed = ParsedLine(product_text="fortune mustard oil", pack_size="1l", quantity=4)
    result = resolve_sku(parsed, sample_skus, threshold=0.80)

    assert result.status == "MATCHED"
    assert result.best == ("SKU-07", "Fortune Mustard Oil 1L", 1.0)
    assert result.needs_pack is False


def test_multiple_pack_sizes_warning_never_yields_matched(sample_skus: list[SkuDTO]) -> None:
    # From real line_parser output ("1kg 500g tata salt")
    parsed_multi = parse_line("1kg 500g tata salt")
    res_multi = resolve_sku(parsed_multi, sample_skus, threshold=0.80)
    assert res_multi.status == "AMBIGUOUS"
    assert res_multi.best is None
    assert res_multi.needs_pack is True

    # Hand-built ParsedLine carrying a pack_size plus the warning
    parsed_filtered = ParsedLine(
        product_text="tata salt",
        pack_size="1kg",
        quantity=2,
        warnings=["multiple pack sizes"],
    )
    res_filtered = resolve_sku(parsed_filtered, sample_skus, threshold=0.80)
    assert res_filtered.status == "AMBIGUOUS"
    assert res_filtered.best is None
    assert res_filtered.needs_pack is True

    # Single-pack product with warning caps at AMBIGUOUS with needs_pack=False
    parsed_single = ParsedLine(
        product_text="aashirvaad atta",
        quantity=2,
        warnings=["multiple pack sizes"],
    )
    res_single = resolve_sku(parsed_single, sample_skus, threshold=0.80)
    assert res_single.status == "AMBIGUOUS"
    assert res_single.best is None
    assert res_single.needs_pack is False


def test_constructed_exact_threshold_and_005_gap_returns_matched() -> None:
    query = "abcdefghijklmnopqrst"
    # 16/20 -> 0.80 (== threshold); 15/20 -> 0.75 (exact 0.05 gap); 14/20 -> 0.70.
    constructed = [
        _make_sku("SKU-A", "BrandA", "ProdA", "1kg", ["abcdefghijklmnop1234"]),
        _make_sku("SKU-B", "BrandB", "ProdB", "1kg", ["abcdefghijklmno12345"]),
        _make_sku("SKU-C", "BrandC", "ProdC", "1kg", ["abcdefghijklmn123456"]),
    ]

    result = resolve_sku(ParsedLine(product_text=query, quantity=1), constructed, threshold=0.80)

    assert result.status == "MATCHED"
    assert result.best == ("SKU-A", "BrandA ProdA 1kg", 0.80)
    assert [c.score for c in result.candidates] == [0.80, 0.75, 0.70]


def test_candidates_never_exceed_three(sample_skus: list[SkuDTO]) -> None:
    parsed = ParsedLine(product_text="atta", quantity=None)
    result = resolve_sku(parsed, sample_skus, threshold=0.80)

    assert len(result.candidates) <= 3
    assert len(result.candidates) == 3
    scores = [c.score for c in result.candidates]
    assert scores == sorted(scores, reverse=True)


def test_end_to_end_parse_line_into_resolve_sku(sample_skus: list[SkuDTO]) -> None:
    parsed_full = parse_line("5 cartons tata salt 1kg")
    result_full = resolve_sku(parsed_full, sample_skus)

    assert parsed_full.quantity == 5
    assert parsed_full.unit == "carton"
    assert result_full.status == "MATCHED"
    assert result_full.best == ("SKU-01", "Tata Salt 1kg", 1.0)

    # Quantity None must not block matching
    parsed_no_qty = parse_line("tata salt 1kg")
    result_no_qty = resolve_sku(parsed_no_qty, sample_skus)
    assert parsed_no_qty.quantity is None
    assert result_no_qty.status == "MATCHED"
    assert result_no_qty.best == ("SKU-01", "Tata Salt 1kg", 1.0)


def test_threshold_reads_from_get_settings_when_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed = [
        _make_sku("SKU-A", "BrandA", "ProdA", "1kg", ["abcdefghijklmnop1234"]),
        _make_sku("SKU-B", "BrandB", "ProdB", "1kg", ["abcdefghijkl12345678"]),
    ]
    monkeypatch.setenv("TARS_SKU_MATCH_THRESHOLD", "0.90")
    get_settings.cache_clear()
    try:
        result = resolve_sku(
            ParsedLine(product_text="abcdefghijklmnopqrst", quantity=1),
            constructed,
        )
        assert result.status == "AMBIGUOUS"
        assert result.best is None
        assert result.candidates[0].score == pytest.approx(0.80)
    finally:
        get_settings.cache_clear()
