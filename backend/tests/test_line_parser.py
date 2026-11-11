"""Tests for pure spoken order-fragment line parsing."""

import pytest

from app.matching.line_parser import HINDI_NUMBER_WORDS, parse_line
from app.matching.normalize import normalize_text


def test_cartons_tata_salt() -> None:
    result = parse_line(normalize_text("5 cartons tata salt"))

    assert result.quantity == 5
    assert result.unit == "carton"
    assert result.pack_size is None
    assert result.product_text == "tata salt"
    assert result.warnings == []


def test_bori_aashirvaad() -> None:
    result = parse_line(normalize_text("2 bori aashirvaad"))

    assert result.quantity == 2
    assert result.unit == "bag"
    assert result.pack_size is None
    assert result.product_text == "aashirvaad"
    assert result.warnings == []


def test_hindi_number_do_bag_atta() -> None:
    result = parse_line(normalize_text("do bag atta"))

    assert result.quantity == 2
    assert result.unit == "bag"
    assert result.product_text == "atta"
    assert result.warnings == []


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("ek", 1),
        ("do", 2),
        ("teen", 3),
        ("char", 4),
        ("paanch", 5),
        ("chhe", 6),
        ("saat", 7),
        ("aath", 8),
        ("nau", 9),
        ("das", 10),
    ],
)
def test_all_hindi_number_words_map_correctly(word: str, expected: int) -> None:
    assert HINDI_NUMBER_WORDS[word] == expected
    result = parse_line(f"{word} packet maggi")
    assert result.quantity == expected
    assert result.unit == "bag"
    assert result.product_text == "maggi"


def test_multiple_quantities_and_mixed_units_return_none_with_warnings() -> None:
    result = parse_line(normalize_text("5 cartons 2 bags tata salt"))

    assert result.quantity is None
    assert "multiple quantities" in result.warnings
    assert result.unit is None
    assert "multiple units" in result.warnings
    assert result.product_text == "tata salt"


def test_identical_repeated_units_keep_unit() -> None:
    result = parse_line(normalize_text("5 cartons case tata salt"))

    assert result.quantity == 5
    assert result.unit == "carton"
    assert result.product_text == "tata salt"
    assert result.warnings == []


def test_multiple_pack_sizes_returns_none_with_warning() -> None:
    result = parse_line(normalize_text("1kg 500g tata salt"))

    assert result.pack_size is None
    assert "multiple pack sizes" in result.warnings
    assert result.product_text == "tata salt"


def test_pack_size_1kg_extracted_and_removed() -> None:
    result = parse_line(normalize_text("tata salt 1kg"))

    assert result.pack_size == "1kg"
    assert result.product_text == "tata salt"
    assert result.quantity is None
    assert "no quantity" in result.warnings


def test_pack_size_1_litre_normalised_to_1l() -> None:
    result = parse_line(normalize_text("tata salt 1 litre"))

    assert result.pack_size == "1l"
    assert result.product_text == "tata salt"
    assert result.quantity is None
    assert "no quantity" in result.warnings


@pytest.mark.parametrize(
    ("fragment", "expected_pack"),
    [
        ("3 cartons oil 500 g", "500g"),
        ("4 piece juice 200ml", "200ml"),
        ("2 case water 1l", "1l"),
        ("1 pc phenyl 1.5 litre", "1.5l"),
    ],
)
def test_pack_size_variants_and_unit_synonyms(fragment: str, expected_pack: str) -> None:
    result = parse_line(normalize_text(fragment))

    assert result.pack_size == expected_pack
    assert result.quantity is not None
    assert result.warnings == []


def test_no_quantity_sets_none_and_warning() -> None:
    result = parse_line(normalize_text("tata salt"))

    assert result.quantity is None
    assert result.unit is None
    assert result.pack_size is None
    assert result.product_text == "tata salt"
    assert result.warnings == ["no quantity"]


def test_zero_quantity_rejected_with_warning() -> None:
    result = parse_line(normalize_text("0 cartons tata salt"))

    assert result.quantity is None
    assert result.unit == "carton"
    assert result.product_text == "tata salt"
    assert result.warnings


@pytest.mark.parametrize("raw_qty", ["500", "501"])
def test_500_and_above_quantity_rejected_with_warning(raw_qty: str) -> None:
    result = parse_line(normalize_text(f"{raw_qty} cartons x"))

    assert result.quantity is None
    assert result.unit == "carton"
    assert result.product_text == "x"
    assert result.warnings


def test_max_valid_quantity_499_accepted() -> None:
    result = parse_line("499 nos soap")

    assert result.quantity == 499
    assert result.unit == "piece"
    assert result.product_text == "soap"
    assert result.warnings == []


def test_filler_words_and_bhej_do_laga_do_do_not_collide_with_do_quantity() -> None:
    result = parse_line(normalize_text("  5 cartons tata salt 1kg ke liye bhej do  "))
    assert result.quantity == 5
    assert result.unit == "carton"
    assert result.pack_size == "1kg"
    assert result.product_text == "tata salt"
    assert result.warnings == []

    hindi_result = parse_line(normalize_text("do bags aashirvaad ko aur chahiye laga do wala wali"))
    assert hindi_result.quantity == 2
    assert hindi_result.unit == "bag"
    assert hindi_result.product_text == "aashirvaad"
    assert hindi_result.warnings == []


def test_five_cartons_tata_salt_bhej_do() -> None:
    result = parse_line(normalize_text("5 cartons tata salt bhej do"))

    assert result.quantity == 5
    assert "multiple quantities" not in result.warnings
    assert result.unit == "carton"
    assert result.product_text == "tata salt"
    assert result.warnings == []


def test_do_bag_atta_bhej_do() -> None:
    result = parse_line(normalize_text("do bag atta bhej do"))

    assert result.quantity == 2
    assert result.unit == "bag"
    assert result.product_text == "atta"
    assert result.warnings == []

