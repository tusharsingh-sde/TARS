"""Pure order-fragment parser extracting quantity, unit, pack size, and product text."""

import re
from typing import Final, Literal

from pydantic import BaseModel, Field

from app.matching.normalize import normalize_text

UnitName = Literal["carton", "bag", "piece"]

MIN_QUANTITY: Final[int] = 1
# Reject 0 and 500+ (valid integer range is 1..499 per spec and boundary test).
MAX_QUANTITY_EXCLUSIVE: Final[int] = 500

HINDI_NUMBER_WORDS: Final[dict[str, int]] = {
    "ek": 1,
    "do": 2,
    "teen": 3,
    "char": 4,
    "paanch": 5,
    "chhe": 6,
    "saat": 7,
    "aath": 8,
    "nau": 9,
    "das": 10,
}

TANGLISH_NUMBER_WORDS: Final[dict[str, int]] = {
    "onnu": 1,
    "oru": 1,
    "rendu": 2,
    "moonu": 3,
    "naalu": 4,
    "anju": 5,
    "aaru": 6,
    "ezhu": 7,
    "ettu": 8,
    "ombadhu": 9,
    "onpadhu": 9,
    "pathu": 10,
}

MARATHI_NUMBER_WORDS: Final[dict[str, int]] = {
    "don": 2,
    "paach": 5,
    "saha": 6,
    "daha": 10,
}

NUMBER_WORDS: Final[dict[str, int]] = {
    **HINDI_NUMBER_WORDS,
    **TANGLISH_NUMBER_WORDS,
    **MARATHI_NUMBER_WORDS,
}

UNIT_MAP: Final[dict[str, UnitName]] = {
    "carton": "carton",
    "cartons": "carton",
    "case": "carton",
    "cases": "carton",
    "box": "carton",
    "boxes": "carton",
    "peti": "carton",
    "petis": "carton",
    "kattu": "carton",
    "bag": "bag",
    "bags": "bag",
    "bori": "bag",
    "packet": "bag",
    "packets": "bag",
    "pishvi": "bag",
    "piece": "piece",
    "pieces": "piece",
    "pc": "piece",
    "nos": "piece",
    "dabba": "piece",
}

PACK_UNIT_MAP: Final[dict[str, str]] = {
    "kg": "kg",
    "kilo": "kg",
    "kilos": "kg",
    "g": "g",
    "gm": "g",
    "gms": "g",
    "gram": "g",
    "grams": "g",
    "ml": "ml",
    "l": "l",
    "ltr": "l",
    "ltrs": "l",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
}

# Multi-word phrases precede single words so "bhej do" / "laga do" are removed before "do".
# Tanglish and Marathi/Minglish conversational fillers stripped cleanly.
FILLER_WORDS: Final[tuple[str, ...]] = (
    "ke liye",
    "bhej do",
    "laga do",
    "kudunga",
    "podunga",
    "annachi",
    "pathva",
    "pahije",
    "chahiye",
    "kudu",
    "podu",
    "venum",
    "taka",
    "wala",
    "wali",
    "aur",
    "dya",
    "ko",
)

_PACK_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(kilos?|kg|gms?|grams?|g|ml|litres?|liters?|ltrs?|l)\b"
)
_FILLER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:" + "|".join(re.escape(item) for item in FILLER_WORDS) + r")\b"
)
_INT_PATTERN: Final[re.Pattern[str]] = re.compile(r"^-?\d+$")


class ParsedLine(BaseModel):
    """Structured fields parsed from a single spoken order fragment."""

    product_text: str
    quantity: int | None = None
    unit: UnitName | None = None
    pack_size: str | None = None
    warnings: list[str] = Field(default_factory=list)


def _extract_pack_size(text: str, warnings: list[str]) -> tuple[str | None, str]:
    """Extract normalized pack size (e.g. '1kg', '500g', '1l') and strip pack tokens."""
    packs: list[str] = []

    def _collect(match: re.Match[str]) -> str:
        number_part = match.group(1)
        unit_part = PACK_UNIT_MAP[match.group(2)]
        packs.append(f"{number_part}{unit_part}")
        return " "

    cleaned = _PACK_PATTERN.sub(_collect, text)
    unique_packs = list(dict.fromkeys(packs))
    if len(unique_packs) > 1:
        warnings.append("multiple pack sizes")
        return None, normalize_text(cleaned)
    return (unique_packs[0] if unique_packs else None), normalize_text(cleaned)


def _extract_quantities_units_and_product(
    tokens: list[str],
) -> tuple[list[int], list[UnitName], list[str]]:
    """Classify normalized tokens into quantities, units, and remaining product tokens."""
    quantities: list[int] = []
    units: list[UnitName] = []
    product_tokens: list[str] = []

    for token in tokens:
        if _INT_PATTERN.match(token):
            quantities.append(int(token))
        elif token in NUMBER_WORDS:
            quantities.append(NUMBER_WORDS[token])
        elif token in UNIT_MAP:
            units.append(UNIT_MAP[token])
        else:
            product_tokens.append(token)

    return quantities, units, product_tokens


def _resolve_quantity(quantities: list[int], warnings: list[str]) -> int | None:
    """Validate extracted quantity list without guessing on missing/multiple/invalid values."""
    if not quantities:
        warnings.append("no quantity")
        return None
    if len(quantities) > 1:
        warnings.append("multiple quantities")
        return None

    qty = quantities[0]
    if qty < MIN_QUANTITY or qty >= MAX_QUANTITY_EXCLUSIVE:
        warnings.append(f"quantity out of range: {qty}")
        return None
    return qty


def _resolve_unit(units: list[UnitName], warnings: list[str]) -> UnitName | None:
    """Return the single extracted unit, or None with a warning if conflicting units appear."""
    unique_units = list(dict.fromkeys(units))
    if len(unique_units) > 1:
        warnings.append("multiple units")
        return None
    return unique_units[0] if unique_units else None


def parse_line(fragment: str) -> ParsedLine:
    """Parse one normalized (or raw) spoken order fragment into structured fields."""
    warnings: list[str] = []
    normalized = normalize_text(fragment)

    pack_size, without_pack = _extract_pack_size(normalized, warnings)
    without_fillers = normalize_text(_FILLER_PATTERN.sub(" ", without_pack))
    tokens = without_fillers.split() if without_fillers else []

    quantities, units, product_tokens = _extract_quantities_units_and_product(tokens)
    quantity = _resolve_quantity(quantities, warnings)
    unit = _resolve_unit(units, warnings)

    return ParsedLine(
        product_text=" ".join(product_tokens),
        quantity=quantity,
        unit=unit,
        pack_size=pack_size,
        warnings=warnings,
    )

