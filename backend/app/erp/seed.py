import csv
import re
from datetime import date
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.erp.models import Base, Outlet, OutletAlias, SKU, SKUAlias, Scheme

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = REPO_ROOT / "data"
_PLAIN_INT_RE = re.compile(r"^-?\d+$")

_OUTLET_COLUMNS = (
    "outlet_id",
    "name",
    "owner_name",
    "locality",
    "city",
    "route_code",
    "credit_limit_paise",
    "outstanding_paise",
    "aliases",
)
_CATALOG_COLUMNS = (
    "sku_id",
    "brand",
    "product",
    "pack_size",
    "unit",
    "mrp_paise",
    "base_price_paise",
    "stock_units",
    "aliases",
)
_SCHEME_COLUMNS = (
    "scheme_id",
    "sku_id",
    "min_qty",
    "discount_bps",
    "valid_from",
    "valid_to",
    "description",
)


class SeedDataError(ValueError):
    """Raised when seed CSV data fails validation or integer paise parsing."""


class TableCounts(BaseModel):
    """Inserted, updated, and final row counts for a single seeded table."""

    inserted: int
    updated: int
    total: int


class SeedSummary(BaseModel):
    """Summary of row counts across all seeded ERP tables."""

    outlets: TableCounts
    outlet_aliases: TableCounts
    skus: TableCounts
    sku_aliases: TableCounts
    schemes: TableCounts


def parse_aliases(raw_aliases: str) -> list[str]:
    """Parse pipe-separated aliases into unique normalized lowercase strings."""
    seen: dict[str, None] = {}
    for part in raw_aliases.split("|"):
        normalized = part.strip().lower()
        if normalized:
            seen[normalized] = None
    return list(seen)


def _require_text(raw: str | None, *, filename: str, row_num: int, column: str) -> str:
    if raw is None or not raw.strip():
        raise SeedDataError(f"{filename} row {row_num}, column '{column}': expected non-empty text")
    return raw.strip()


def _parse_plain_int(
    raw: str | None,
    *,
    filename: str,
    row_num: int,
    column: str,
    allow_zero: bool = True,
) -> int:
    if raw is None:
        raise SeedDataError(f"{filename} row {row_num}, column '{column}': missing integer value")
    stripped = raw.strip()
    if not _PLAIN_INT_RE.fullmatch(stripped):
        raise SeedDataError(
            f"{filename} row {row_num}, column '{column}': expected plain integer string, got {raw!r}"
        )
    value = int(stripped)
    if value < 0 or (not allow_zero and value == 0):
        bound = "non-negative" if allow_zero else "positive"
        raise SeedDataError(
            f"{filename} row {row_num}, column '{column}': expected {bound} integer, got {value}"
        )
    return value


def _parse_iso_date(raw: str | None, *, filename: str, row_num: int, column: str) -> date:
    text = _require_text(raw, filename=filename, row_num=row_num, column=column)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise SeedDataError(
            f"{filename} row {row_num}, column '{column}': invalid ISO date {raw!r}"
        ) from exc


def _read_csv_rows(csv_path: Path, expected_columns: tuple[str, ...]) -> list[dict[str, str]]:
    if not csv_path.exists():
        raise SeedDataError(f"Missing seed CSV file: {csv_path.name}")
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SeedDataError(f"{csv_path.name}: CSV file has no header row")
        missing = [col for col in expected_columns if col not in reader.fieldnames]
        if missing:
            raise SeedDataError(f"{csv_path.name}: missing columns {', '.join(missing)}")
        return list(reader)


def _count_rows(session: Session, model: type[Base]) -> int:
    count = session.scalar(select(func.count()).select_from(model))
    return int(count or 0)


def _seed_outlets(session: Session, csv_path: Path) -> tuple[TableCounts, TableCounts]:
    filename = csv_path.name
    rows = _read_csv_rows(csv_path, _OUTLET_COLUMNS)
    inserted = 0
    updated = 0
    aliases_inserted = 0

    for row_num, row in enumerate(rows, start=1):
        outlet_id = _require_text(row.get("outlet_id"), filename=filename, row_num=row_num, column="outlet_id")
        name = _require_text(row.get("name"), filename=filename, row_num=row_num, column="name")
        owner_name = _require_text(row.get("owner_name"), filename=filename, row_num=row_num, column="owner_name")
        locality = _require_text(row.get("locality"), filename=filename, row_num=row_num, column="locality")
        city = _require_text(row.get("city"), filename=filename, row_num=row_num, column="city")
        route_code = _require_text(row.get("route_code"), filename=filename, row_num=row_num, column="route_code")
        credit_limit_paise = _parse_plain_int(
            row.get("credit_limit_paise"),
            filename=filename,
            row_num=row_num,
            column="credit_limit_paise",
        )
        outstanding_paise = _parse_plain_int(
            row.get("outstanding_paise"),
            filename=filename,
            row_num=row_num,
            column="outstanding_paise",
        )
        aliases = parse_aliases(row.get("aliases") or "")

        existing = session.get(Outlet, outlet_id)
        if existing is None:
            session.add(
                Outlet(
                    outlet_id=outlet_id,
                    name=name,
                    owner_name=owner_name,
                    locality=locality,
                    city=city,
                    route_code=route_code,
                    credit_limit_paise=credit_limit_paise,
                    outstanding_paise=outstanding_paise,
                )
            )
            inserted += 1
        else:
            existing.name = name
            existing.owner_name = owner_name
            existing.locality = locality
            existing.city = city
            existing.route_code = route_code
            existing.credit_limit_paise = credit_limit_paise
            existing.outstanding_paise = outstanding_paise
            session.execute(delete(OutletAlias).where(OutletAlias.outlet_id == outlet_id))
            updated += 1

        session.flush()
        for alias in aliases:
            session.add(OutletAlias(outlet_id=outlet_id, alias=alias))
            aliases_inserted += 1

    session.flush()
    outlet_counts = TableCounts(inserted=inserted, updated=updated, total=_count_rows(session, Outlet))
    alias_counts = TableCounts(inserted=aliases_inserted, updated=0, total=_count_rows(session, OutletAlias))
    return outlet_counts, alias_counts


def _seed_skus(session: Session, csv_path: Path) -> tuple[TableCounts, TableCounts]:
    filename = csv_path.name
    rows = _read_csv_rows(csv_path, _CATALOG_COLUMNS)
    inserted = 0
    updated = 0
    aliases_inserted = 0

    for row_num, row in enumerate(rows, start=1):
        sku_id = _require_text(row.get("sku_id"), filename=filename, row_num=row_num, column="sku_id")
        brand = _require_text(row.get("brand"), filename=filename, row_num=row_num, column="brand")
        product = _require_text(row.get("product"), filename=filename, row_num=row_num, column="product")
        pack_size = _require_text(row.get("pack_size"), filename=filename, row_num=row_num, column="pack_size")
        unit = _require_text(row.get("unit"), filename=filename, row_num=row_num, column="unit")
        mrp_paise = _parse_plain_int(
            row.get("mrp_paise"),
            filename=filename,
            row_num=row_num,
            column="mrp_paise",
            allow_zero=False,
        )
        base_price_paise = _parse_plain_int(
            row.get("base_price_paise"),
            filename=filename,
            row_num=row_num,
            column="base_price_paise",
            allow_zero=False,
        )
        stock_units = _parse_plain_int(
            row.get("stock_units"),
            filename=filename,
            row_num=row_num,
            column="stock_units",
        )
        aliases = parse_aliases(row.get("aliases") or "")

        existing = session.get(SKU, sku_id)
        if existing is None:
            session.add(
                SKU(
                    sku_id=sku_id,
                    brand=brand,
                    product=product,
                    pack_size=pack_size,
                    unit=unit,
                    mrp_paise=mrp_paise,
                    base_price_paise=base_price_paise,
                    stock_units=stock_units,
                )
            )
            inserted += 1
        else:
            existing.brand = brand
            existing.product = product
            existing.pack_size = pack_size
            existing.unit = unit
            existing.mrp_paise = mrp_paise
            existing.base_price_paise = base_price_paise
            existing.stock_units = stock_units
            session.execute(delete(SKUAlias).where(SKUAlias.sku_id == sku_id))
            updated += 1

        session.flush()
        for alias in aliases:
            session.add(SKUAlias(sku_id=sku_id, alias=alias))
            aliases_inserted += 1

    session.flush()
    sku_counts = TableCounts(inserted=inserted, updated=updated, total=_count_rows(session, SKU))
    alias_counts = TableCounts(inserted=aliases_inserted, updated=0, total=_count_rows(session, SKUAlias))
    return sku_counts, alias_counts


def _seed_schemes(session: Session, csv_path: Path) -> TableCounts:
    filename = csv_path.name
    rows = _read_csv_rows(csv_path, _SCHEME_COLUMNS)
    inserted = 0
    updated = 0

    for row_num, row in enumerate(rows, start=1):
        scheme_id = _require_text(row.get("scheme_id"), filename=filename, row_num=row_num, column="scheme_id")
        sku_id = _require_text(row.get("sku_id"), filename=filename, row_num=row_num, column="sku_id")
        if session.get(SKU, sku_id) is None:
            raise SeedDataError(
                f"{filename} row {row_num}, column 'sku_id': unknown SKU {sku_id!r}"
            )
        min_qty = _parse_plain_int(
            row.get("min_qty"),
            filename=filename,
            row_num=row_num,
            column="min_qty",
            allow_zero=False,
        )
        discount_bps = _parse_plain_int(
            row.get("discount_bps"),
            filename=filename,
            row_num=row_num,
            column="discount_bps",
        )
        valid_from = _parse_iso_date(
            row.get("valid_from"), filename=filename, row_num=row_num, column="valid_from"
        )
        valid_to = _parse_iso_date(
            row.get("valid_to"), filename=filename, row_num=row_num, column="valid_to"
        )
        description = _require_text(
            row.get("description"), filename=filename, row_num=row_num, column="description"
        )

        existing = session.get(Scheme, scheme_id)
        if existing is None:
            session.add(
                Scheme(
                    scheme_id=scheme_id,
                    sku_id=sku_id,
                    min_qty=min_qty,
                    discount_bps=discount_bps,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    description=description,
                )
            )
            inserted += 1
        else:
            existing.sku_id = sku_id
            existing.min_qty = min_qty
            existing.discount_bps = discount_bps
            existing.valid_from = valid_from
            existing.valid_to = valid_to
            existing.description = description
            updated += 1

    session.flush()
    return TableCounts(inserted=inserted, updated=updated, total=_count_rows(session, Scheme))


def _seed_in_session(session: Session, data_dir: Path) -> SeedSummary:
    outlets, outlet_aliases = _seed_outlets(session, data_dir / "outlets.csv")
    skus, sku_aliases = _seed_skus(session, data_dir / "catalog.csv")
    schemes = _seed_schemes(session, data_dir / "schemes.csv")
    return SeedSummary(
        outlets=outlets,
        outlet_aliases=outlet_aliases,
        skus=skus,
        sku_aliases=sku_aliases,
        schemes=schemes,
    )


def seed_database(
    target: str | Session | None = None,
    data_dir: Path | None = None,
) -> SeedSummary:
    """Create ERP tables if needed and seed CSV data atomically in one transaction."""
    resolved_dir = data_dir if data_dir is not None else DEFAULT_DATA_DIR

    if isinstance(target, Session):
        bind = target.get_bind()
        if not target.in_transaction():
            Base.metadata.create_all(bind)
            with target.begin():
                return _seed_in_session(target, resolved_dir)
        Base.metadata.create_all(bind)
        with target.begin_nested():
            return _seed_in_session(target, resolved_dir)

    database_url = target if target is not None else get_settings().database_url
    engine = create_engine(database_url)
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            with session.begin():
                return _seed_in_session(session, resolved_dir)
    finally:
        engine.dispose()


def format_summary(summary: SeedSummary) -> str:
    """Format per-table seed counts without leaking connection details."""
    lines = [
        f"outlets: inserted={summary.outlets.inserted}, updated={summary.outlets.updated}, total={summary.outlets.total}",
        f"outlet_aliases: inserted={summary.outlet_aliases.inserted}, updated={summary.outlet_aliases.updated}, total={summary.outlet_aliases.total}",
        f"skus: inserted={summary.skus.inserted}, updated={summary.skus.updated}, total={summary.skus.total}",
        f"sku_aliases: inserted={summary.sku_aliases.inserted}, updated={summary.sku_aliases.updated}, total={summary.sku_aliases.total}",
        f"schemes: inserted={summary.schemes.inserted}, updated={summary.schemes.updated}, total={summary.schemes.total}",
    ]
    return "\n".join(lines)


def main() -> None:
    """CLI entry point for `python -m app.erp.seed`."""
    summary = seed_database()
    print(format_summary(summary))


if __name__ == "__main__":
    main()

