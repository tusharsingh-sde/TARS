import csv
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
REFERENCE_DATE = date(2026, 11, 11)


def _load_csv(filename: str) -> tuple[list[str], list[dict[str, str]]]:
    path = DATA_DIR / filename
    assert path.exists(), f"Missing seed CSV: {path}"
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames is not None
        rows = list(reader)
        return list(reader.fieldnames), rows


def test_outlets_seed_data() -> None:
    headers, rows = _load_csv("outlets.csv")
    assert headers == [
        "outlet_id",
        "name",
        "owner_name",
        "locality",
        "city",
        "route_code",
        "credit_limit_paise",
        "outstanding_paise",
        "aliases",
    ]
    assert len(rows) == 30

    ids = [row["outlet_id"] for row in rows]
    assert len(set(ids)) == 30
    assert ids == [f"OUT-{i:03d}" for i in range(1, 31)]

    high_utilization_count = 0
    for row in rows:
        assert row["name"].strip() != ""
        assert row["owner_name"].strip() != ""
        assert row["locality"].strip() != ""
        assert row["city"].strip() != ""
        assert row["route_code"] in {"R1", "R2", "R3", "R4"}

        credit_limit = int(row["credit_limit_paise"])
        outstanding = int(row["outstanding_paise"])
        assert 2_000_000 <= credit_limit <= 15_000_000
        assert 0 <= outstanding <= credit_limit

        if outstanding * 10 >= credit_limit * 9:
            high_utilization_count += 1

        aliases = row["aliases"].split("|")
        assert len(aliases) >= 2
        assert all(alias.strip() != "" for alias in aliases)

    assert high_utilization_count >= 3


def test_catalog_seed_data() -> None:
    headers, rows = _load_csv("catalog.csv")
    assert headers == [
        "sku_id",
        "brand",
        "product",
        "pack_size",
        "unit",
        "mrp_paise",
        "base_price_paise",
        "stock_units",
        "aliases",
    ]
    assert len(rows) == 80

    ids = [row["sku_id"] for row in rows]
    assert len(set(ids)) == 80
    assert ids == [f"SKU-{i:03d}" for i in range(1, 81)]

    brands = {row["brand"].strip() for row in rows}
    assert len(brands) >= 8

    zero_stock_count = 0
    for row in rows:
        assert row["brand"].strip() != ""
        assert row["product"].strip() != ""
        assert row["pack_size"].strip() != ""
        assert row["unit"] in {"carton", "bag", "piece"}

        mrp_paise = int(row["mrp_paise"])
        base_price_paise = int(row["base_price_paise"])
        stock_units = int(row["stock_units"])

        assert mrp_paise > 0
        assert base_price_paise > 0
        assert base_price_paise <= mrp_paise
        assert stock_units >= 0
        if stock_units == 0:
            zero_stock_count += 1

        aliases = row["aliases"].split("|")
        assert len(aliases) >= 2
        assert all(alias.strip() != "" for alias in aliases)

    assert zero_stock_count == 4


def test_schemes_seed_data() -> None:
    _, catalog_rows = _load_csv("catalog.csv")
    catalog_sku_ids = {row["sku_id"] for row in catalog_rows}

    headers, rows = _load_csv("schemes.csv")
    assert headers == [
        "scheme_id",
        "sku_id",
        "min_qty",
        "discount_bps",
        "valid_from",
        "valid_to",
        "description",
    ]
    assert len(rows) == 10

    scheme_ids = [row["scheme_id"] for row in rows]
    assert len(set(scheme_ids)) == 10

    active_on_reference = 0
    for row in rows:
        assert row["sku_id"] in catalog_sku_ids
        min_qty = int(row["min_qty"])
        discount_bps = int(row["discount_bps"])
        assert min_qty > 0
        assert 50 <= discount_bps <= 500

        valid_from = date.fromisoformat(row["valid_from"])
        valid_to = date.fromisoformat(row["valid_to"])
        assert valid_from <= valid_to
        if valid_from <= REFERENCE_DATE <= valid_to:
            active_on_reference += 1

        assert row["description"].strip() != ""

    assert active_on_reference >= 7

