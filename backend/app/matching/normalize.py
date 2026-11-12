"""Text normalization helpers for voice-matched entities."""

import unicodedata
from typing import Final

# Fixed Devanagari-to-Roman word map built from catalog terms and parser number/unit maps.
DEVANAGARI_TO_ROMAN: Final[dict[str, str]] = {
    # Required core catalog, pack, unit, and Hindi number words
    "भूमि": "bhoomi",
    "सॉल्ट": "salt",
    "साल्ट": "salt",
    "नमक": "namak",
    "किलो": "kilo",
    "ग्राम": "g",
    "कार्टन": "carton",
    "पेटी": "peti",
    "बोरी": "bori",
    "एक": "ek",
    "दो": "do",
    "तीन": "teen",
    "चार": "char",
    "पांच": "paanch",
    "छह": "chhe",
    "सात": "saat",
    "आठ": "aath",
    "नौ": "nau",
    "दस": "das",
    "सुर्या": "surya",
    "सूर्या": "surya",
    "ग्लूकोज": "glucose",
    "बिस्कुट": "biscuit",
    "आशीर्वाद": "aashirvaad",
    "आटा": "atta",
    # Catalog & line_parser unit/product terms
    "बैग": "bag",
    "पैकेट": "packet",
    "पीस": "piece",
    "डब्बा": "dabba",
    "बॉक्स": "box",
    "लीटर": "litre",
    "हल्दी": "haldi",
    "दाल": "dal",
    "चावल": "chawal",
    "तेल": "tel",
    "घी": "ghee",
    "दूध": "doodh",
    "चाय": "chai",
    "पनीर": "paneer",
    "साबुन": "sabun",
    "नूडल्स": "noodles",
    "पोहा": "poha",
    "बेसन": "besan",
    "शहद": "shahad",
    "अचार": "achar",
    "मसाला": "masala",
}

_DEVANAGARI_NUKTA: Final[str] = "\u093c"
_DEVANAGARI_CHANDRABINDU: Final[str] = "\u0901"
_DEVANAGARI_ANUSVARA: Final[str] = "\u0902"


def _devanagari_lookup_key(token: str) -> str:
    """Strip nukta (U+093C) and normalize chandrabindu (U+0901) for fixed-table lookup."""
    return token.replace(_DEVANAGARI_NUKTA, "").replace(
        _DEVANAGARI_CHANDRABINDU, _DEVANAGARI_ANUSVARA
    )


def normalize_text(value: str) -> str:
    """Normalize NFC, map known Devanagari tokens to Roman, lowercase, and collapse spaces."""
    nfc = unicodedata.normalize("NFC", value)
    mapped_tokens: list[str] = []
    for token in nfc.split():
        roman = DEVANAGARI_TO_ROMAN.get(token)
        if roman is None:
            roman = DEVANAGARI_TO_ROMAN.get(_devanagari_lookup_key(token))
        mapped_tokens.append(roman if roman is not None else token)
    return " ".join(mapped_tokens).lower()
