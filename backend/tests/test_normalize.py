"""Tests for Unicode normalization and fixed Devanagari-to-Roman mapping."""

import unicodedata

import pytest

from app.matching.normalize import DEVANAGARI_TO_ROMAN, normalize_text


def test_devanagari_utterance_normalizes_to_roman() -> None:
    assert normalize_text("5 भूमि सॉल्ट 1 किलो") == "5 bhoomi salt 1 kilo"
    assert normalize_text("5 भूमि साल्ट एक किलो") == "5 bhoomi salt ek kilo"
    assert normalize_text("दो पेटी सुर्या ग्लूकोज 100 ग्राम") == "do peti surya glucose 100 g"
    assert normalize_text("तीन बोरी आशीर्वाद आटा 5 किलो") == "teen bori aashirvaad atta 5 kilo"
    assert normalize_text("चार कार्टन नमक") == "char carton namak"
    assert normalize_text("छह सात आठ नौ दस बिस्कुट") == "chhe saat aath nau das biscuit"


def test_devanagari_nukta_and_chandrabindu_variants_map_cleanly() -> None:
    # Nukta U+093C on ज in ग्लूकोज़ maps to glucose; chandrabindu U+0901 on पाँच maps to paanch.
    assert normalize_text("पाँच पेटी सुर्या ग्लूको\u091c\u093c बिस्कुट") == (
        "paanch peti surya glucose biscuit"
    )
    assert DEVANAGARI_TO_ROMAN["ग्लूकोज"] == "glucose"
    assert DEVANAGARI_TO_ROMAN["पांच"] == "paanch"


def test_pure_latin_input_is_unchanged() -> None:
    assert normalize_text("  5 Cartons Bhoomi Salt 1KG  ") == "5 cartons bhoomi salt 1kg"
    assert normalize_text("do bag atta") == "do bag atta"


def test_unknown_devanagari_words_left_unchanged() -> None:
    assert normalize_text("5 अनजानवर्ड 1 किलो") == "5 अनजानवर्ड 1 kilo"


@pytest.mark.parametrize(
    "raw",
    [
        "5 भूमि सॉल्ट 1 किलो",
        "5 carton bhoomi salt ek kilo",
        unicodedata.normalize("NFD", "आशीर्वाद आटा पाँच किलो"),
        "5 अनजान 1 किलो",
    ],
)
def test_normalize_text_is_idempotent(raw: str) -> None:
    once = normalize_text(raw)
    twice = normalize_text(once)
    assert twice == once

