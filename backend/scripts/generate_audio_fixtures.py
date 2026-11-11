"""Generate deterministic 16kHz 16-bit mono PCM test audio fixtures for TARS demo & tests."""

from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPWIDTH = 2  # 16-bit signed PCM
MAX_INT16 = 32767
MIN_INT16 = -32768


def _clamp(val: float) -> int:
    """Clamp float to 16-bit signed integer range."""
    return max(MIN_INT16, min(MAX_INT16, int(round(val))))


def generate_noisy_shop_audio(duration_sec: float = 1.0) -> bytes:
    """Simulate shop audio with 50Hz mains hum, ambient chatter noise, and speech formants."""
    total_samples = int(SAMPLE_RATE * duration_sec)
    rng = random.Random(42)  # Deterministic seed
    frames = bytearray()

    for i in range(total_samples):
        t = i / SAMPLE_RATE
        # 50Hz mains hum
        hum = 1800.0 * math.sin(2.0 * math.pi * 50.0 * t)
        # Background ambient noise / hiss
        noise = rng.uniform(-1500.0, 1500.0)

        # Speech envelope: active mostly between 0.15s and 0.85s
        if 0.15 <= t <= 0.85:
            rel = (t - 0.15) / 0.70
            env = math.sin(math.pi * rel)
            # Simulated vowel formants (F1 ~350Hz, F2 ~900Hz, F3 ~2400Hz)
            vocal = (
                6000.0 * math.sin(2.0 * math.pi * 350.0 * t)
                + 4000.0 * math.sin(2.0 * math.pi * 900.0 * t)
                + 2500.0 * math.sin(2.0 * math.pi * 2400.0 * t)
            )
            speech = vocal * env
        else:
            speech = 0.0

        sample_val = _clamp(hum + noise + speech)
        frames.extend(struct.pack("<h", sample_val))

    return bytes(frames)


def generate_clipped_low_snr_audio(duration_sec: float = 1.0) -> bytes:
    """Simulate low SNR speech buried in harsh shop chatter with peak clipping."""
    total_samples = int(SAMPLE_RATE * duration_sec)
    rng = random.Random(99)  # Deterministic seed
    frames = bytearray()

    for i in range(total_samples):
        t = i / SAMPLE_RATE
        # Heavy noise floor
        noise = rng.uniform(-9000.0, 9000.0)
        # Weak speech signal (low SNR)
        speech = 2500.0 * math.sin(2.0 * math.pi * 440.0 * t)

        combined = noise + speech

        # Artificial peak clipping bursts every 200ms
        if int(t * 10) % 2 == 0 and rng.random() < 0.08:
            combined = 45000.0 if rng.random() > 0.5 else -45000.0

        sample_val = _clamp(combined)
        frames.extend(struct.pack("<h", sample_val))

    return bytes(frames)


def generate_barge_in_audio(duration_sec: float = 1.0) -> bytes:
    """Simulate agent prompt speech followed by sudden rep interruption/barge-in."""
    total_samples = int(SAMPLE_RATE * duration_sec)
    rng = random.Random(123)  # Deterministic seed
    frames = bytearray()

    for i in range(total_samples):
        t = i / SAMPLE_RATE

        if t < 0.40:
            # Steady agent readback audio
            prompt_tone = (
                4000.0 * math.sin(2.0 * math.pi * 440.0 * t)
                + 2000.0 * math.sin(2.0 * math.pi * 880.0 * t)
            )
            sample_val = _clamp(prompt_tone + rng.uniform(-300.0, 300.0))
        elif t < 0.50:
            # Brief pause / silence before interruption
            sample_val = _clamp(rng.uniform(-200.0, 200.0))
        else:
            # Sudden high-amplitude rep voice interruption
            rel = (t - 0.50) / 0.50
            env = min(1.0, rel * 5.0)  # Sharp attack
            voice = (
                14000.0 * math.sin(2.0 * math.pi * 280.0 * t)
                + 8000.0 * math.sin(2.0 * math.pi * 650.0 * t)
            )
            sample_val = _clamp(voice * env + rng.uniform(-800.0, 800.0))

        frames.extend(struct.pack("<h", sample_val))

    return bytes(frames)


def save_wav(path: Path, pcm_data: bytes) -> None:
    """Save raw 16-bit mono 16kHz PCM bytes to standard RIFF/WAVE file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPWIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_data)


def generate_all_fixtures(output_dir: Path) -> dict[str, Path]:
    """Generate the three standard test clips in output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)

    fixtures = {
        "noisy_shop_order": output_dir / "noisy_shop_order_16k.wav",
        "clipped_low_snr": output_dir / "clipped_low_snr_16k.wav",
        "barge_in_correction": output_dir / "barge_in_correction_16k.wav",
    }

    save_wav(fixtures["noisy_shop_order"], generate_noisy_shop_audio())
    save_wav(fixtures["clipped_low_snr"], generate_clipped_low_snr_audio())
    save_wav(fixtures["barge_in_correction"], generate_barge_in_audio())

    return fixtures


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent.parent
    recordings_dir = repo_root / "demo" / "recordings"
    generated = generate_all_fixtures(recordings_dir)
    for name, path in generated.items():
        print(f"Generated {name}: {path} ({path.stat().st_size} bytes)")

