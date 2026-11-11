"""Manual smoke script to verify a Gemini Live session without audio hardware."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from google.genai.live import AsyncSession
from websockets.exceptions import WebSocketException

TIMEOUT_SECONDS = 30.0
SMOKE_PROMPT = "Reply with the word ready."


def _ensure_backend_on_path_and_load_env() -> None:
    """Allow running from repo root or backend/ and load root .env defaults."""
    backend_dir = Path(__file__).resolve().parent.parent
    repo_root = backend_dir.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    load_dotenv(repo_root / ".env", override=False)


def _redact_secret(message: str, secret: str) -> str:
    """Remove the API key from any diagnostic or error string."""
    if not secret:
        return message
    return message.replace(secret, "[REDACTED]")


def _extract_chunk_sizes(content: types.LiveServerContent) -> list[tuple[int, str]]:
    """Return (size, unit) entries for text or audio payload chunks in one event."""
    chunks: list[tuple[int, str]] = []
    if content.output_transcription and content.output_transcription.text:
        chunks.append((len(content.output_transcription.text), "chars"))
    if content.model_turn and content.model_turn.parts:
        for part in content.model_turn.parts:
            if part.text:
                chunks.append((len(part.text), "chars"))
            elif part.inline_data and part.inline_data.data:
                chunks.append((len(part.inline_data.data), "bytes"))
    return chunks


async def _run_live_smoke(api_key: str, model: str) -> tuple[int, int, str, float]:
    """Open a Live session, send a single text prompt, and measure first-chunk timing."""
    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
    # Per ADR-004, native audio models (gemini-3.8-live) only support AUDIO response
    # modality and emit text via output_audio_transcription; no audio hardware is used.
    config: types.LiveConnectConfigDict = {
        "response_modalities": [types.Modality.AUDIO],
        "output_audio_transcription": {},
    }

    session: AsyncSession | None = None
    exit_stack = contextlib.AsyncExitStack()
    chunk_count = 0
    first_chunk_size = 0
    first_chunk_unit = "chars"
    first_chunk_ms: float | None = None

    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            session = await exit_stack.enter_async_context(
                client.aio.live.connect(model=model, config=config)
            )
            started_at = time.perf_counter()
            await session.send_realtime_input(text=SMOKE_PROMPT)

            async for response in session.receive():
                if not response.server_content:
                    continue
                for size, unit in _extract_chunk_sizes(response.server_content):
                    chunk_count += 1
                    if first_chunk_ms is None:
                        first_chunk_ms = (time.perf_counter() - started_at) * 1000.0
                        first_chunk_size = size
                        first_chunk_unit = unit
    finally:
        if session is not None:
            try:
                await session.close()
            except (OSError, WebSocketException) as close_exc:
                safe_close = _redact_secret(str(close_exc), api_key)
                print(
                    f"Warning during session close ({type(close_exc).__name__}): {safe_close}",
                    file=sys.stderr,
                )
        await exit_stack.aclose()

    if first_chunk_ms is None:
        first_chunk_ms = 0.0
    return chunk_count, first_chunk_size, first_chunk_unit, first_chunk_ms


def main() -> int:
    """Validate configuration, run the live smoke session, and print timing stats."""
    _ensure_backend_on_path_and_load_env()
    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    api_key = settings.gemini_api_key.strip()
    if not api_key:
        print("TARS_GEMINI_API_KEY is not set in .env", file=sys.stderr)
        return 2

    try:
        chunks, first_size, first_unit, first_ms = asyncio.run(
            _run_live_smoke(api_key=api_key, model=settings.gemini_model)
        )
    except TimeoutError:
        print(
            f"Gemini Live smoke test timed out after {int(TIMEOUT_SECONDS)}s.",
            file=sys.stderr,
        )
        return 1
    except (
        genai_errors.APIError,
        WebSocketException,
        OSError,
        ValueError,
        RuntimeError,
    ) as exc:
        safe_detail = _redact_secret(str(exc), api_key)
        print(
            f"Gemini Live smoke test failed ({type(exc).__name__}): {safe_detail}",
            file=sys.stderr,
        )
        return 1

    if chunks == 0:
        print("Gemini Live session completed with 0 response chunks.", file=sys.stderr)
        return 1

    print(
        f"model={settings.gemini_model} chunks={chunks} "
        f"first_chunk_size={first_size} {first_unit} "
        f"time_to_first_chunk_ms={first_ms:.1f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
