import contextlib
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from google.genai import types

from app.voice import (
    CHANNELS,
    INPUT_MIME_TYPE,
    INPUT_SAMPLE_RATE,
    OUTPUT_MIME_TYPE,
    OUTPUT_SAMPLE_RATE,
    SAMPLE_WIDTH_BYTES,
    SYSTEM_INSTRUCTION,
    AudioChunkEvent,
    BargeInEvent,
    GeminiLiveBridge,
    ToolCallRequestEvent,
    TranscriptEvent,
    TurnCompleteEvent,
    build_system_prompt,
    chunk_pcm16,
    pcm_duration_ms,
    validate_pcm16_frame,
)


class FakeClock:
    """Deterministic monotonic clock helper in seconds."""

    def __init__(self, initial_seconds: float = 10.0) -> None:
        self.now = initial_seconds

    def __call__(self) -> float:
        return self.now

    def advance_ms(self, ms: int) -> None:
        self.now += ms / 1000.0


class FakeLiveSession:
    """In-memory fake async Gemini Live session for deterministic testing."""

    def __init__(self, batches: Sequence[Sequence[types.LiveServerMessage]]) -> None:
        self._batches = [list(batch) for batch in batches]
        self.realtime_inputs: list[dict[str, Any]] = []
        self.tool_responses: list[list[types.FunctionResponse]] = []
        self.closed = False

    async def send_realtime_input(
        self,
        *,
        audio: types.Blob | None = None,
        text: str | None = None,
    ) -> None:
        self.realtime_inputs.append({"audio": audio, "text": text})

    async def send_tool_response(
        self,
        *,
        function_responses: Sequence[types.FunctionResponse] | types.FunctionResponse,
    ) -> None:
        if isinstance(function_responses, types.FunctionResponse):
            self.tool_responses.append([function_responses])
        else:
            self.tool_responses.append(list(function_responses))

    async def receive(self) -> AsyncIterator[types.LiveServerMessage]:
        if not self._batches:
            return
        batch = self._batches.pop(0)
        for message in batch:
            yield message

    async def close(self) -> None:
        self.closed = True


def _audio_message(
    pcm_bytes: bytes,
    *,
    turn_complete: bool = False,
    output_text: str | None = None,
) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(
            model_turn=types.Content(
                parts=[
                    types.Part(
                        inline_data=types.Blob(data=pcm_bytes, mime_type=OUTPUT_MIME_TYPE)
                    )
                ]
            ),
            output_transcription=(
                types.Transcription(text=output_text, finished=turn_complete)
                if output_text is not None
                else None
            ),
            turn_complete=turn_complete,
        )
    )


def test_audio_constants_and_validation() -> None:
    assert INPUT_SAMPLE_RATE == 16000
    assert OUTPUT_SAMPLE_RATE == 24000
    assert CHANNELS == 1
    assert SAMPLE_WIDTH_BYTES == 2
    assert INPUT_MIME_TYPE == "audio/pcm;rate=16000"
    assert OUTPUT_MIME_TYPE == "audio/pcm;rate=24000"

    frame = b"\x01\x02" * 1600  # 3200 bytes = 100ms at 16kHz
    assert validate_pcm16_frame(frame) == frame
    assert pcm_duration_ms(frame) == 100
    assert pcm_duration_ms(b"\x00\x00" * 2400, sample_rate=OUTPUT_SAMPLE_RATE) == 100
    assert pcm_duration_ms(b"") == 0

    with pytest.raises(ValueError, match="empty"):
        validate_pcm16_frame(b"")
    with pytest.raises(ValueError, match="multiple of 2"):
        validate_pcm16_frame(b"\x01\x02\x03")
    with pytest.raises(ValueError, match="positive"):
        pcm_duration_ms(frame, sample_rate=0)
    with pytest.raises(ValueError, match="multiple of 2"):
        pcm_duration_ms(b"\x01")


def test_chunk_pcm16_splits_aligned_chunks_and_guards_invalid() -> None:
    data = b"\xaa\xbb" * 4000  # 8000 bytes
    chunks = chunk_pcm16(data, chunk_bytes=3200)
    assert [len(chunk) for chunk in chunks] == [3200, 3200, 1600]
    assert b"".join(chunks) == data

    with pytest.raises(ValueError, match="chunk_bytes"):
        chunk_pcm16(data, chunk_bytes=0)
    with pytest.raises(ValueError, match="chunk_bytes"):
        chunk_pcm16(data, chunk_bytes=3)
    with pytest.raises(ValueError, match="empty"):
        chunk_pcm16(b"")
    with pytest.raises(ValueError, match="multiple of 2"):
        chunk_pcm16(b"\x01\x02\x03")


def test_system_prompt_enforces_hinglish_readback_and_never_guess() -> None:
    lowered = SYSTEM_INSTRUCTION.lower()
    assert "hinglish" in lowered
    assert "roman/latin" in lowered
    assert "never guess" in lowered
    assert "ambiguous" in lowered
    assert "not_found" in lowered
    assert "read back" in lowered
    assert "outlet name" in lowered
    assert "haan" in lowered
    assert "confirm" in lowered
    assert "until the commit tool succeeds" in lowered

    assert build_system_prompt() == SYSTEM_INSTRUCTION
    assert build_system_prompt(rep_id="   ", route_code="") == SYSTEM_INSTRUCTION

    contextual = build_system_prompt(rep_id="REP-07", route_code="DEL-S1")
    assert "Sales Rep ID: REP-07" in contextual
    assert "Route Code: DEL-S1" in contextual


@pytest.mark.asyncio
async def test_session_factory_receives_adr004_config_and_guards_errors() -> None:
    captured: list[tuple[str, types.LiveConnectConfigDict]] = []
    fake_session = FakeLiveSession(batches=[[]])

    @contextlib.asynccontextmanager
    async def fake_factory(
        model: str,
        config: types.LiveConnectConfigDict,
    ) -> AsyncIterator[FakeLiveSession]:
        captured.append((model, config))
        yield fake_session

    tool_decl: types.ToolDict = {"function_declarations": [{"name": "resolve_outlet"}]}
    bridge = GeminiLiveBridge(
        model="gemini-3.8-live",
        tools=[tool_decl],
        session_factory=fake_factory,
    )

    with pytest.raises(RuntimeError, match="not connected"):
        await bridge.send_audio_chunk(b"\x00\x01\x02\x03")

    async with bridge:
        assert bridge.is_connected is True
        with pytest.raises(ValueError, match="empty"):
            await bridge.send_text_turn("   ")
        with pytest.raises(ValueError, match="empty"):
            await bridge.send_audio_chunk(b"")

    assert fake_session.closed is True
    assert bridge.is_connected is False
    assert len(captured) == 1
    model_used, config_used = captured[0]
    assert model_used == "gemini-3.8-live"
    assert config_used["response_modalities"] == [types.Modality.AUDIO]
    assert config_used["input_audio_transcription"] == {}
    assert config_used["output_audio_transcription"] == {}
    assert config_used["system_instruction"] == SYSTEM_INSTRUCTION
    assert config_used["tools"] == [tool_decl]


@pytest.mark.asyncio
async def test_audio_turn_records_ttfa_and_emits_events() -> None:
    clock = FakeClock(initial_seconds=1.0)
    first_pcm = b"\x01\x02" * 600
    second_pcm = b"\x03\x04" * 800

    async def scripted_receive(session: FakeLiveSession) -> AsyncIterator[types.LiveServerMessage]:
        yield types.LiveServerMessage(
            server_content=types.LiveServerContent(
                input_transcription=types.Transcription(
                    text="Sharma ji 5 carton salt",
                    finished=True,
                )
            )
        )
        clock.advance_ms(240)
        yield _audio_message(first_pcm, output_text="Sharma Kirana ke liye ")
        clock.advance_ms(60)
        yield _audio_message(
            second_pcm,
            turn_complete=True,
            output_text="5 carton Tata Salt 1kg?",
        )

    fake_session = FakeLiveSession(batches=[])
    fake_session.receive = lambda: scripted_receive(fake_session)  # type: ignore[method-assign]

    bridge = GeminiLiveBridge(session=fake_session, clock=clock)
    async with bridge:
        await bridge.send_audio_chunk(b"\x10\x20" * 800)  # t = 1000ms
        clock.advance_ms(40)
        await bridge.send_audio_chunk(b"\x30\x40" * 800)  # timer stays at 1000ms

        events = [event async for event in bridge.iter_events()]

    assert len(fake_session.realtime_inputs) == 2
    first_blob = fake_session.realtime_inputs[0]["audio"]
    assert isinstance(first_blob, types.Blob)
    assert first_blob.mime_type == INPUT_MIME_TYPE

    assert isinstance(events[0], TranscriptEvent)
    assert events[0].role == "user"
    assert events[0].is_final is True

    assert isinstance(events[1], TranscriptEvent)
    assert events[1].role == "model"
    assert events[1].is_final is False

    assert isinstance(events[2], AudioChunkEvent)
    assert events[2].pcm_bytes == first_pcm
    assert events[2].sample_rate == OUTPUT_SAMPLE_RATE

    assert isinstance(events[3], TranscriptEvent)
    assert events[3].is_final is True

    assert isinstance(events[4], AudioChunkEvent)
    assert events[4].pcm_bytes == second_pcm

    assert isinstance(events[5], TurnCompleteEvent)
    metrics = events[5].metrics
    assert metrics.turn_id == 1
    assert metrics.started_at_ms == 1000
    assert metrics.first_audio_at_ms == 1280
    assert metrics.time_to_first_audio_ms == 280
    assert metrics.audio_bytes_sent == 3200
    assert metrics.audio_bytes_received == len(first_pcm) + len(second_pcm)
    assert metrics.interrupted is False
    assert bridge.completed_turns == [metrics]


@pytest.mark.asyncio
async def test_barge_in_emits_event_and_marks_turn_interrupted() -> None:
    clock = FakeClock(initial_seconds=5.0)
    barge_in_msg = types.LiveServerMessage(
        server_content=types.LiveServerContent(interrupted=True, turn_complete=True)
    )
    fake_session = FakeLiveSession(batches=[[barge_in_msg]])
    bridge = GeminiLiveBridge(session=fake_session, clock=clock)

    async with bridge:
        await bridge.send_text_turn("Read back order")
        clock.advance_ms(175)
        events = [event async for event in bridge.iter_events()]

    assert isinstance(events[0], BargeInEvent)
    assert events[0].interrupted_at_ms == 5175
    assert isinstance(events[1], TurnCompleteEvent)
    assert events[1].metrics.interrupted is True
    assert events[1].metrics.time_to_first_audio_ms is None
    assert bridge.completed_turns[0].interrupted is True


@pytest.mark.asyncio
async def test_tool_call_and_response_resets_post_tool_ttfa_timer() -> None:
    clock = FakeClock(initial_seconds=2.0)
    post_tool_audio = b"\x05\x06" * 400

    tool_msg = types.LiveServerMessage(
        tool_call=types.LiveServerToolCall(
            function_calls=[
                types.FunctionCall(
                    id="call-42",
                    name="resolve_outlet",
                    args={"query": "sharma ji"},
                )
            ]
        )
    )
    post_tool_msg = _audio_message(
        post_tool_audio,
        turn_complete=True,
        output_text="Sharma Kirana Store mila hai.",
    )

    fake_session = FakeLiveSession(batches=[[tool_msg], [post_tool_msg]])
    bridge = GeminiLiveBridge(session=fake_session, clock=clock)

    collected: list[object] = []
    async with bridge:
        await bridge.send_text_turn("Sharma ji ke liye order likho")
        clock.advance_ms(90)
        async for event in bridge.iter_events(continuous=True):
            collected.append(event)
            if isinstance(event, ToolCallRequestEvent):
                clock.advance_ms(35)
                await bridge.send_tool_response(
                    call_id=event.call_id,
                    name=event.name,
                    response={"status": "MATCHED", "outlet_id": "OUT-001"},
                )
                clock.advance_ms(110)

    assert len(fake_session.tool_responses) == 1
    sent_fn = fake_session.tool_responses[0][0]
    assert sent_fn.id == "call-42"
    assert sent_fn.name == "resolve_outlet"
    assert sent_fn.response == {"status": "MATCHED", "outlet_id": "OUT-001"}

    assert isinstance(collected[0], ToolCallRequestEvent)
    assert collected[0].call_id == "call-42"
    assert collected[0].name == "resolve_outlet"
    assert collected[0].args == {"query": "sharma ji"}

    turn_done = collected[-1]
    assert isinstance(turn_done, TurnCompleteEvent)
    assert turn_done.metrics.started_at_ms == 2125
    assert turn_done.metrics.first_audio_at_ms == 2235
    assert turn_done.metrics.time_to_first_audio_ms == 110


@pytest.mark.asyncio
async def test_connect_without_api_key_raises_clear_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("TARS_GEMINI_API_KEY", "")
    get_settings.cache_clear()
    try:
        bridge = GeminiLiveBridge(model="gemini-3.8-live")
        with pytest.raises(ValueError, match="TARS_GEMINI_API_KEY"):
            await bridge.connect()
    finally:
        get_settings.cache_clear()
