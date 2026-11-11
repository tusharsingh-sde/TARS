"""Text normalization helpers for voice-matched entities."""


def normalize_text(value: str) -> str:
    """Lowercase text and collapse whitespace runs to single ASCII spaces."""
    return " ".join(value.split()).lower()

