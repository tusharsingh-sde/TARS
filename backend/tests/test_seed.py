import csv
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.erp.models import Base, Outlet, OutletAlias, SKU, SKUAlias, Scheme
from app.erp.seed import DEFAULT_DATA_DIR, SeedDataError, format_summary, seed_database


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def test_seed_first_and_second_run_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "seed_test.db"
    db_url = f"sqlite:///{db_path}"

    first = seed_database(db_url)
    assert first.outlets.inserted == 30
    assert first.outlets.updated == 0
    assert first.outlets.total == 30
    assert first.skus.inserted == 80
    assert first.skus.updated == 0
    assert first.skus.total == 80
    assert first.schemes.inserted == 10
    assert first.schemes.updated == 0
    assert first.schemes.total == 10

    second = seed_database(db_url)
    assert second.outlets.inserted == 0
    assert second.outlets.updated == 30
    assert second.outlets.total == 30
    assert second.outlet_aliases.total == first.outlet_aliases.total
    assert second.skus.inserted == 0
    assert second.skus.updated == 80
    assert second.skus.total == 80
    assert second.sku_aliases.total == first.sku_aliases.total
    assert second.schemes.inserted == 0
    assert second.schemes.updated == 10
    assert second.schemes.total == 10

    assert "sqlite" not in format_summary(second).lower()


def test_seed_aliases_lowercase_unique_and_replaced_with_session(tmp_path: Path) -> None:
    _write_csv(
        tmp_path / "outlets.csv",
        [
            "outlet_id",
            "name",
            "owner_name",
            "locality",
            "city",
            "route_code",
            "credit_limit_paise",
            "outstanding_paise",
            "aliases",
        ],
        [
            [
                "OUT-901",
                "Test Kirana",
                "Amit Kumar",
                "Shastri Nagar",
                "Meerut",
                "R1",
                "2500000",
                "100000",
                " Sharma Ji | SHARMA JI |  | Test Store  |   ",
            ]
        ],
    )
    _write_csv(
        tmp_path / "catalog.csv",
        [
            "sku_id",
            "brand",
            "product",
            "pack_size",
            "unit",
            "mrp_paise",
            "base_price_paise",
            "stock_units",
            "aliases",
        ],
        [
            [
                "SKU-901",
                "TestBrand",
                "Test Biscuit",
                "100g",
                "carton",
                "72000",
                "64000",
                "50",
                " Biscuit 100g |  | BISCUIT 100G | Parle Style ",
            ]
        ],
    )
    _write_csv(
        tmp_path / "schemes.csv",
        ["scheme_id", "sku_id", "min_qty", "discount_bps", "valid_from", "valid_to", "description"],
        [["SCH-901", "SKU-901", "5", "250", "2026-10-01", "2026-12-31", "Test scheme"]],
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'session_seed.db'}")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            summary = seed_database(session, data_dir=tmp_path)
            assert summary.outlet_aliases.total == 2
            assert summary.sku_aliases.total == 2

            outlet_aliases = session.scalars(
                select(OutletAlias.alias).where(OutletAlias.outlet_id == "OUT-901")
            ).all()
            sku_aliases = session.scalars(
                select(SKUAlias.alias).where(SKUAlias.sku_id == "SKU-901")
            ).all()
            assert outlet_aliases == ["sharma ji", "test store"]
            assert sku_aliases == ["biscuit 100g", "parle style"]

        _write_csv(
            tmp_path / "outlets.csv",
            [
                "outlet_id",
                "name",
                "owner_name",
                "locality",
                "city",
                "route_code",
                "credit_limit_paise",
                "outstanding_paise",
                "aliases",
            ],
            [
                [
                    "OUT-901",
                    "Test Kirana Updated",
                    "Amit Kumar",
                    "Shastri Nagar",
                    "Meerut",
                    "R1",
                    "3000000",
                    "100000",
                    " New Alias | new alias ",
                ]
            ],
        )

        with Session(engine) as session:
            summary2 = seed_database(session, data_dir=tmp_path)
            assert summary2.outlets.inserted == 0
            assert summary2.outlets.updated == 1
            assert summary2.outlet_aliases.total == 1
            updated_aliases = session.scalars(
                select(OutletAlias.alias).where(OutletAlias.outlet_id == "OUT-901")
            ).all()
            assert updated_aliases == ["new alias"]
    finally:
        engine.dispose()


def test_real_seed_aliases_lowercase_and_unique_per_parent(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'real_aliases.db'}"
    seed_database(db_url)

    engine = create_engine(db_url)
    try:
        with Session(engine) as session:
            outlet_pairs = session.execute(select(OutletAlias.outlet_id, OutletAlias.alias)).all()
            sku_pairs = session.execute(select(SKUAlias.sku_id, SKUAlias.alias)).all()

            assert outlet_pairs
            assert sku_pairs
            assert all(alias == alias.strip().lower() and alias != "" for _, alias in outlet_pairs)
            assert all(alias == alias.strip().lower() and alias != "" for _, alias in sku_pairs)
            assert len(set(outlet_pairs)) == len(outlet_pairs)
            assert len(set(sku_pairs)) == len(sku_pairs)
    finally:
        engine.dispose()


@pytest.mark.parametrize("bad_value", ["12.50", "abc", "", "-50", "1_000"])
def test_malformed_paise_raises_and_writes_nothing(tmp_path: Path, bad_value: str) -> None:
    # Populate valid outlets first so rollback across CSV files is verified.
    (tmp_path / "outlets.csv").write_text(
        (DEFAULT_DATA_DIR / "outlets.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _write_csv(
        tmp_path / "catalog.csv",
        [
            "sku_id",
            "brand",
            "product",
            "pack_size",
            "unit",
            "mrp_paise",
            "base_price_paise",
            "stock_units",
            "aliases",
        ],
        [
            [
                "SKU-901",
                "TestBrand",
                "Test Biscuit",
                "100g",
                "carton",
                bad_value,
                "64000",
                "10",
                "test biscuit|tb",
            ]
        ],
    )
    (tmp_path / "schemes.csv").write_text(
        (DEFAULT_DATA_DIR / "schemes.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    db_url = f"sqlite:///{tmp_path / 'rollback.db'}"
    with pytest.raises(SeedDataError) as exc_info:
        seed_database(db_url, data_dir=tmp_path)

    message = str(exc_info.value)
    assert "catalog.csv" in message
    assert "row 1" in message
    assert "mrp_paise" in message

    engine = create_engine(db_url)
    try:
        with Session(engine) as session:
            assert (session.scalar(select(func.count()).select_from(Outlet)) or 0) == 0
            assert (session.scalar(select(func.count()).select_from(OutletAlias)) or 0) == 0
            assert (session.scalar(select(func.count()).select_from(SKU)) or 0) == 0
            assert (session.scalar(select(func.count()).select_from(SKUAlias)) or 0) == 0
            assert (session.scalar(select(func.count()).select_from(Scheme)) or 0) == 0
    finally:
        engine.dispose()

