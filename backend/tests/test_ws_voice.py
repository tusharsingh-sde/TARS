"""Tests for ws_voice WebSocket event forwarding and raw PCM serialization."""

from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from google.genai import types

from app.main import _forward_bridge_events, app
from app.voice.gemini_live import (
    AudioChunkEvent,
    GeminiLiveBridge,
    TranscriptEvent,
)


class _RecordingWebSocket:
    """Minimal WebSocket test double capturing send_json payloads."""

    def __init__(self) -> None:
        self.sent_json: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent_json.append(data)


class _FakeBridge:
    """Fake bridge yielding pre-canned events for _forward_bridge_events."""

    def __init__(self, events: Sequence[Any]) -> None:
        self._events = list(events)

    async def iter_events(self, *, continuous: bool = False) -> AsyncIterator[Any]:
        del continuous
        for event in self._events:
            yield event


class _UnserializableEvent:
    """Non-audio event stub whose model_dump fails."""

    type = "broken_event"

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        del mode
        raise UnicodeDecodeError("utf-8", b"\xfa", 0, 1, "invalid start byte")


class _FakeLiveSession:
    """In-memory fake Gemini Live session yielding raw non-UTF-8 PCM and a transcript."""

    def __init__(self, batches: Sequence[Sequence[types.LiveServerMessage]]) -> None:
        self._batches = [list(batch) for batch in batches]

    async def send_realtime_input(
        self,
        *,
        audio: types.Blob | None = None,
        text: str | None = None,
    ) -> None:
        del audio, text

    async def send_tool_response(
        self,
        *,
        function_responses: Sequence[types.FunctionResponse] | types.FunctionResponse,
    ) -> None:
        del function_responses

    async def receive(self) -> AsyncIterator[types.LiveServerMessage]:
        if not self._batches:
            return
        for message in self._batches.pop(0):
            yield message

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_forward_bridge_events_handles_non_utf8_audio_and_continues() -> None:
    raw_pcm = b"\xfa\xfe\xf1\x00\x80\xff"
    fake_bridge = _FakeBridge(
        events=[
            AudioChunkEvent(pcm_bytes=raw_pcm, sample_rate=24000),
            TranscriptEvent(role="model", text="Sharma Kirana Bhandar", is_final=True),
        ]
    )
    ws = _RecordingWebSocket()

    await _forward_bridge_events(
        bridge=fake_bridge,  # type: ignore[arg-type]
        websocket=ws,  # type: ignore[arg-type]
        orch=None,  # type: ignore[arg-type]
        db=None,  # type: ignore[arg-type]
    )

    assert len(ws.sent_json) == 2
    audio_msg = ws.sent_json[0]
    assert audio_msg["type"] == "audio_out"
    assert audio_msg["sample_rate"] == 24000
    assert "pcm_bytes" not in audio_msg
    assert base64.b64decode(audio_msg["pcm_b64"]) == raw_pcm

    text_msg = ws.sent_json[1]
    assert text_msg == {
        "type": "transcript",
        "role": "model",
        "text": "Sharma Kirana Bhandar",
        "is_final": True,
    }


@pytest.mark.asyncio
async def test_forward_bridge_events_skips_bad_event_without_closing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_bridge = _FakeBridge(
        events=[
            _UnserializableEvent(),
            TranscriptEvent(role="model", text="Order ready", is_final=True),
        ]
    )
    ws = _RecordingWebSocket()

    with caplog.at_level(logging.WARNING):
        await _forward_bridge_events(
            bridge=fake_bridge,  # type: ignore[arg-type]
            websocket=ws,  # type: ignore[arg-type]
            orch=None,  # type: ignore[arg-type]
            db=None,  # type: ignore[arg-type]
        )

    assert len(ws.sent_json) == 1
    assert ws.sent_json[0]["type"] == "transcript"
    assert ws.sent_json[0]["text"] == "Order ready"
    assert "broken_event" in caplog.text


def test_ws_voice_route_streams_non_utf8_audio_and_subsequent_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_pcm = b"\xfa\xfe\xf1\x00\x80\xff"
    fake_session = _FakeLiveSession(
        batches=[
            [
                types.LiveServerMessage(
                    server_content=types.LiveServerContent(
                        model_turn=types.Content(
                            parts=[
                                types.Part(
                                    inline_data=types.Blob(
                                        data=raw_pcm, mime_type="audio/pcm;rate=24000"
                                    )
                                )
                            ]
                        ),
                    )
                ),
                types.LiveServerMessage(
                    server_content=types.LiveServerContent(
                        output_transcription=types.Transcription(
                            text="Sharma Kirana Bhandar", finished=True
                        ),
                    )
                ),
            ]
        ]
    )

    def _fake_bridge_factory(**kwargs: Any) -> GeminiLiveBridge:
        return GeminiLiveBridge(
            api_key=kwargs.get("api_key"),
            model=kwargs.get("model"),
            tools=kwargs.get("tools"),
            session=fake_session,
        )

    monkeypatch.setattr("app.main.GeminiLiveBridge", _fake_bridge_factory)

    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/voice/test-audio-sess") as ws,
    ):
        first = ws.receive_json()
        second = ws.receive_json()

    assert first["type"] == "audio_out"
    assert first["sample_rate"] == 24000
    assert base64.b64decode(first["pcm_b64"]) == raw_pcm
    assert second == {
        "type": "transcript",
        "role": "model",
        "text": "Sharma Kirana Bhandar",
        "is_final": True,
    }
