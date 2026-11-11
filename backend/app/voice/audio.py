from typing import Final

INPUT_SAMPLE_RATE: Final[int] = 16000
OUTPUT_SAMPLE_RATE: Final[int] = 24000
CHANNELS: Final[int] = 1
SAMPLE_WIDTH_BYTES: Final[int] = 2
DEFAULT_CHUNK_BYTES: Final[int] = 3200
INPUT_MIME_TYPE: Final[str] = "audio/pcm;rate=16000"
OUTPUT_MIME_TYPE: Final[str] = "audio/pcm;rate=24000"


def validate_pcm16_frame(data: bytes) -> bytes:
    """Validate non-empty 16-bit aligned little-endian PCM bytes."""
    if not data:
        raise ValueError("PCM16 audio frame must not be empty")
    if len(data) % SAMPLE_WIDTH_BYTES != 0:
        raise ValueError(
            f"PCM16 audio frame length must be a multiple of {SAMPLE_WIDTH_BYTES} bytes, "
            f"got {len(data)}"
        )
    return data


def pcm_duration_ms(data: bytes, sample_rate: int = INPUT_SAMPLE_RATE) -> int:
    """Compute integer millisecond duration for aligned mono PCM16 audio."""
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if len(data) % SAMPLE_WIDTH_BYTES != 0:
        raise ValueError(
            f"PCM16 audio length must be a multiple of {SAMPLE_WIDTH_BYTES} bytes, "
            f"got {len(data)}"
        )
    sample_count = len(data) // SAMPLE_WIDTH_BYTES
    return (sample_count * 1000) // sample_rate


def chunk_pcm16(data: bytes, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> list[bytes]:
    """Split validated mono PCM16 bytes into sample-aligned chunks."""
    if chunk_bytes <= 0 or chunk_bytes % SAMPLE_WIDTH_BYTES != 0:
        raise ValueError(
            f"chunk_bytes must be a positive multiple of {SAMPLE_WIDTH_BYTES}, got {chunk_bytes}"
        )
    validated = validate_pcm16_frame(data)
    return [validated[offset : offset + chunk_bytes] for offset in range(0, len(validated), chunk_bytes)]

