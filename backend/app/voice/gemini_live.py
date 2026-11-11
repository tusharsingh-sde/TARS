import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import Any, Literal, Protocol, Self

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.config import get_settings
from app.voice.audio import INPUT_MIME_TYPE, OUTPUT_SAMPLE_RATE, validate_pcm16_frame
from app.voice.prompts import SYSTEM_INSTRUCTION

logger = logging.getLogger(__name__)

LIVE_API_VERSION = "v1beta"


class AudioChunkEvent(BaseModel):
    """PCM16 24kHz audio chunk emitted by the Gemini Live model."""

    type: Literal["audio_out"] = "audio_out"
    pcm_bytes: bytes
    sample_rate: int = OUTPUT_SAMPLE_RATE


class TranscriptEvent(BaseModel):
    """User or model speech transcription chunk."""

    type: Literal["transcript"] = "transcript"
    role: Literal["user", "model"]
    text: str
    is_final: bool = False


class BargeInEvent(BaseModel):
    """Signal that user speech interrupted model output."""

    type: Literal["barge_in"] = "barge_in"
    interrupted_at_ms: int


class ToolCallRequestEvent(BaseModel):
    """Function call requested by the Gemini Live model."""

    type: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    args: dict[str, object]


class TurnMetrics(BaseModel):
    """Latency and byte-count telemetry recorded for one voice turn."""

    turn_id: int
    started_at_ms: int
    first_audio_at_ms: int | None = None
    time_to_first_audio_ms: int | None = None
    audio_bytes_sent: int = 0
    audio_bytes_received: int = 0
    interrupted: bool = False


class TurnCompleteEvent(BaseModel):
    """End-of-turn signal carrying finalized metrics."""

    type: Literal["turn_complete"] = "turn_complete"
    metrics: TurnMetrics


type VoiceBridgeEvent = (
    AudioChunkEvent
    | TranscriptEvent
    | BargeInEvent
    | ToolCallRequestEvent
    | TurnCompleteEvent
)


class LiveSessionProtocol(Protocol):
    """Async Live session interface satisfied by google.genai.live.AsyncSession."""

    async def send_realtime_input(
        self,
        *,
        audio: types.Blob | None = None,
        text: str | None = None,
    ) -> None: ...

    async def send_tool_response(
        self,
        *,
        function_responses: Sequence[types.FunctionResponse] | types.FunctionResponse,
    ) -> None: ...

    def receive(self) -> AsyncIterator[types.LiveServerMessage]: ...

    async def close(self) -> None: ...


type SessionFactory = Callable[
    [str, types.LiveConnectConfigDict],
    AbstractAsyncContextManager[LiveSessionProtocol],
]


class GeminiLiveBridge:
    """Bidirectional Gemini Live session bridge with barge-in and TTFA tracking."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        system_instruction: str = SYSTEM_INSTRUCTION,
        tools: Sequence[types.ToolDict] | None = None,
        session: LiveSessionProtocol | None = None,
        session_factory: SessionFactory | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._system_instruction = system_instruction
        self._tools: list[types.ToolDict] = list(tools) if tools is not None else []
        self._injected_session = session
        self._session_factory = session_factory
        self._clock: Callable[[], float] = clock or time.monotonic

        self._session: LiveSessionProtocol | None = None
        self._exit_stack: contextlib.AsyncExitStack | None = None
        self._closed = False

        self._turn_seq = 0
        self._call_seq = 0
        self._current_turn: TurnMetrics | None = None
        self.completed_turns: list[TurnMetrics] = []

    @property
    def is_connected(self) -> bool:
        """Return whether an active Live session is currently open."""
        return self._session is not None and not self._closed

    @property
    def current_turn(self) -> TurnMetrics | None:
        """Return the active in-progress turn metrics, if any."""
        return self._current_turn

    def build_live_config(self) -> types.LiveConnectConfigDict:
        """Build the LiveConnectConfigDict aligned with ADR-004."""
        config: types.LiveConnectConfigDict = {
            "response_modalities": [types.Modality.AUDIO],
            "input_audio_transcription": {},
            "output_audio_transcription": {},
            "system_instruction": self._system_instruction,
        }
        if self._tools:
            config["tools"] = list(self._tools)
        return config

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def connect(self) -> None:
        """Open the Live session via injected session, factory, or google-genai client."""
        if self._session is not None:
            return
        self._closed = False

        if self._injected_session is not None:
            self._session = self._injected_session
            return

        model = self._resolve_model()
        config = self.build_live_config()
        exit_stack = contextlib.AsyncExitStack()

        try:
            if self._session_factory is not None:
                session_cm = self._session_factory(model, config)
            else:
                api_key = self._resolve_api_key()
                client = genai.Client(
                    api_key=api_key,
                    http_options={"api_version": LIVE_API_VERSION},
                )
                session_cm = client.aio.live.connect(model=model, config=config)

            self._session = await exit_stack.enter_async_context(session_cm)
            self._exit_stack = exit_stack
        except BaseException:
            await exit_stack.aclose()
            raise

    async def close(self) -> None:
        """Close the active Live session and release context resources."""
        self._closed = True
        session = self._session
        self._session = None

        if session is not None:
            with contextlib.suppress(OSError, RuntimeError):
                await session.close()

        if self._exit_stack is not None:
            stack = self._exit_stack
            self._exit_stack = None
            await stack.aclose()

    async def send_audio_chunk(self, pcm_bytes: bytes) -> None:
        """Validate a PCM16 frame, start turn timer if needed, and stream to Gemini Live."""
        validated = validate_pcm16_frame(pcm_bytes)
        session = self._require_session()
        turn = self._ensure_active_turn()
        turn.audio_bytes_sent += len(validated)
        await session.send_realtime_input(
            audio=types.Blob(data=validated, mime_type=INPUT_MIME_TYPE)
        )

    async def send_text_turn(self, text: str) -> None:
        """Start a fresh turn timer and send a realtime text prompt."""
        if not text.strip():
            raise ValueError("text must not be empty")
        session = self._require_session()
        self._start_new_turn()
        await session.send_realtime_input(text=text)

    async def send_tool_response(
        self,
        call_id: str,
        name: str,
        response: dict[str, object],
    ) -> None:
        """Send a FunctionResponse to Gemini Live and reset the post-tool TTFA timer."""
        if not call_id.strip():
            raise ValueError("call_id must not be empty")
        if not name.strip():
            raise ValueError("name must not be empty")
        session = self._require_session()
        self._reset_turn_timer()
        payload: dict[str, Any] = dict(response)
        await session.send_tool_response(
            function_responses=[
                types.FunctionResponse(id=call_id, name=name, response=payload)
            ]
        )

    async def iter_events(self, *, continuous: bool = False) -> AsyncIterator[VoiceBridgeEvent]:
        """Yield normalized bridge events from Gemini Live session responses."""
        session = self._require_session()
        while not self._closed:
            received_any = False
            async for message in session.receive():
                received_any = True
                for event in self._extract_message_events(message):
                    yield event
            if not continuous or not received_any:
                break

    def _resolve_api_key(self) -> str:
        raw_key = self._api_key if self._api_key is not None else get_settings().gemini_api_key
        key = raw_key.strip()
        if not key:
            raise ValueError("TARS_GEMINI_API_KEY is not configured")
        return key

    def _resolve_model(self) -> str:
        raw_model = self._model if self._model is not None else get_settings().gemini_model
        model = raw_model.strip()
        if not model:
            raise ValueError("TARS_GEMINI_MODEL is not configured")
        return model

    def _require_session(self) -> LiveSessionProtocol:
        if self._session is None or self._closed:
            raise RuntimeError("GeminiLiveBridge session is not connected")
        return self._session

    def _now_ms(self) -> int:
        return round(self._clock() * 1000)

    def _start_new_turn(self) -> TurnMetrics:
        self._turn_seq += 1
        turn = TurnMetrics(turn_id=self._turn_seq, started_at_ms=self._now_ms())
        self._current_turn = turn
        return turn

    def _ensure_active_turn(self) -> TurnMetrics:
        if self._current_turn is None:
            return self._start_new_turn()
        return self._current_turn

    def _reset_turn_timer(self) -> TurnMetrics:
        if self._current_turn is None:
            return self._start_new_turn()
        self._current_turn.started_at_ms = self._now_ms()
        self._current_turn.first_audio_at_ms = None
        self._current_turn.time_to_first_audio_ms = None
        return self._current_turn

    def _extract_message_events(self, message: types.LiveServerMessage) -> list[VoiceBridgeEvent]:
        events: list[VoiceBridgeEvent] = []
        if message.server_content is not None:
            events.extend(self._handle_server_content(message.server_content))
        if message.tool_call is not None:
            events.extend(self._handle_tool_call(message.tool_call))
        return events

    def _handle_server_content(self, content: types.LiveServerContent) -> list[VoiceBridgeEvent]:
        events: list[VoiceBridgeEvent] = []
        turn = self._ensure_active_turn()

        if content.interrupted:
            interrupted_at_ms = self._now_ms()
            turn.interrupted = True
            logger.info("Turn %d interrupted at %d ms", turn.turn_id, interrupted_at_ms)
            events.append(BargeInEvent(interrupted_at_ms=interrupted_at_ms))

        events.extend(self._extract_transcriptions(content))
        events.extend(self._extract_audio_parts(content, turn))

        if content.turn_complete:
            metrics = turn.model_copy()
            self.completed_turns.append(metrics)
            self._current_turn = None
            logger.info(
                "Turn %d completed: ttfa_ms=%s sent_bytes=%d recv_bytes=%d interrupted=%s",
                metrics.turn_id,
                metrics.time_to_first_audio_ms,
                metrics.audio_bytes_sent,
                metrics.audio_bytes_received,
                metrics.interrupted,
            )
            events.append(TurnCompleteEvent(metrics=metrics))

        return events

    def _extract_transcriptions(
        self,
        content: types.LiveServerContent,
    ) -> list[VoiceBridgeEvent]:
        events: list[VoiceBridgeEvent] = []
        if content.input_transcription and content.input_transcription.text:
            events.append(
                TranscriptEvent(
                    role="user",
                    text=content.input_transcription.text,
                    is_final=bool(content.input_transcription.finished),
                )
            )
        if content.output_transcription and content.output_transcription.text:
            events.append(
                TranscriptEvent(
                    role="model",
                    text=content.output_transcription.text,
                    is_final=bool(content.output_transcription.finished),
                )
            )
        return events

    def _extract_audio_parts(
        self,
        content: types.LiveServerContent,
        turn: TurnMetrics,
    ) -> list[VoiceBridgeEvent]:
        events: list[VoiceBridgeEvent] = []
        if not content.model_turn or not content.model_turn.parts:
            return events

        for part in content.model_turn.parts:
            if part.inline_data is None or not part.inline_data.data:
                continue
            pcm = part.inline_data.data
            turn.audio_bytes_received += len(pcm)
            if turn.first_audio_at_ms is None:
                now_ms = self._now_ms()
                turn.first_audio_at_ms = now_ms
                turn.time_to_first_audio_ms = max(0, now_ms - turn.started_at_ms)
                logger.info(
                    "Turn %d first audio chunk: ttfa_ms=%d bytes=%d",
                    turn.turn_id,
                    turn.time_to_first_audio_ms,
                    len(pcm),
                )
            events.append(AudioChunkEvent(pcm_bytes=pcm, sample_rate=OUTPUT_SAMPLE_RATE))

        return events

    def _handle_tool_call(self, tool_call: types.LiveServerToolCall) -> list[VoiceBridgeEvent]:
        if not tool_call.function_calls:
            return []
        self._ensure_active_turn()
        events: list[VoiceBridgeEvent] = []
        for fn_call in tool_call.function_calls:
            self._call_seq += 1
            call_id = fn_call.id or f"call-{self._call_seq}"
            name = fn_call.name or ""
            args: dict[str, object] = dict(fn_call.args) if fn_call.args else {}
            events.append(ToolCallRequestEvent(call_id=call_id, name=name, args=args))
        return events
