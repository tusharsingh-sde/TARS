# TARS — Architecture Decision Log

## ADR-001: Normalized Child Tables (`outlet_aliases`, `sku_aliases`) for Aliases
- **Date:** 2026-11-11
- **Task:** P1-03
- **Decision:** Store Hinglish outlet and SKU aliases in dedicated child tables (`outlet_aliases`, `sku_aliases`) with foreign keys and `(parent_id, alias)` unique constraints instead of JSON list columns on `outlets` and `skus`.
- **Rationale:**
  - Enables indexed alias queries and enforces uniqueness per parent record at the database layer.
  - Works identically across SQLite and PostgreSQL without JSON dialect differences.
  - Keeps CSV seeding (`P1-04`) and RapidFuzz index loading clean via SQLAlchemy `relationship` loading.

## ADR-002: Gemini Live API Model, Audio Formats, and SDK Connection Pattern
- **Date:** 2026-11-11
- **Task:** P1-02 (confirmed during P1-06 per P0-05)
- **Decision:**
  - **Model:** `gemini-3.8-live` (Stable GA, latest update September 2026; runs exclusively at `thinkingLevel="MINIMAL"` for lowest time-to-first-audio; use `gemini-3.8-live-extended-thinking` only if configurable thinking depth is needed later).
  - **Input audio format:** Raw, little-endian, 16-bit PCM, natively 16kHz (`audio/pcm;rate=16000` on `types.Blob`; Live API resamples other sample rates if needed).
  - **Output audio format:** Raw, little-endian, 16-bit PCM at 24kHz (`part.inline_data.data` in `response.server_content.model_turn.parts`).
  - **SDK session method (`google-genai` Python SDK):** `async with client.aio.live.connect(model=model, config=config) as session:` with `response_modalities=["AUDIO"]`, sending audio via `await session.send_realtime_input(audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000"))` and receiving chunks via `async for response in session.receive():`.
- **Sources (checked 2026-11-11 via official `gemini-api-docs` / `https://aistudio.google.com/docs/llms.txt`):**
  - `https://aistudio.google.com/docs/models/gemini-3.8-live` (`gemini-api-guides/models/gemini-3.8-live.md`)
  - `https://aistudio.google.com/docs/live-api/capabilities` (`gemini-api-guides/live-api/capabilities.md`)
  - `https://aistudio.google.com/docs/live-api/get-started-sdk` (`gemini-api-guides/live-api/get-started-sdk.md`)

## ADR-003: Defer PWA Service Worker Until After Demo
- **Date:** 2026-11-11
- **Task:** P0-03
- **Decision:** Keep the web app manifest (`frontend/public/manifest.json`) linked from `frontend/index.html`, and defer adding or registering a PWA service worker until after the demo.
- **Rationale:**
  - A service worker can cache stale dev assets or interfere with Vite HMR during development.
  - Intercepting fetch/WebSocket upgrade requests can interfere with live audio streaming; offline caching is not needed for the live voice demo.

## ADR-004: Verified Gemini Live Model, Audio Channels, and Text/Audio Session Config (Supersedes ADR-002)
- **Date:** 2026-11-11
- **Task:** P0-05, P1-02
- **Status:** Supersedes ADR-002
- **Why ADR-002 is superseded:** ADR-002 correctly identified `gemini-3.8-live`, 16-bit LE PCM sample rates, and `client.aio.live.connect`, but omitted channel count (`mono`) and the Live API response-modality restriction for native audio models (`TEXT`-only `response_modalities` is unsupported on native audio models, so text output requires `AUDIO` + `output_audio_transcription`).
- **Verified Specification:**
  - **Model string:** `gemini-3.8-live` (Stable GA, updated September 2026; supersedes deprecated/preview Live models; runs at `thinkingLevel="MINIMAL"`).
  - **Native audio input format:** Raw, little-endian, 16-bit linear PCM, `1` channel (mono), natively `16000 Hz` (`16kHz`); MIME type `audio/pcm;rate=16000` on `types.Blob` (API resamples other input rates if needed).
  - **Native audio output format:** Raw, little-endian, 16-bit linear PCM, `1` channel (mono), `24000 Hz` (`24kHz`).
  - **`google-genai` SDK (`>= 2.3.0`, `3.1.0` installed):**
    - Client: `client = genai.Client(api_key=settings.gemini_api_key, http_options={"api_version": "v1beta"})`
    - Connect: `async with client.aio.live.connect(model=model, config=config) as session:`
    - Audio / text config on native audio models: `config: types.LiveConnectConfigDict = {"response_modalities": [types.Modality.AUDIO], "output_audio_transcription": {}}` (`Modality.AUDIO == "AUDIO"`; read `response.server_content.output_transcription.text` for text transcription because native audio models only support `AUDIO` response modality; `TEXT` response modality is only for non-native-audio models like `gemini-3.5-transcribe-live`).
    - Sending text prompt & closing: `await session.send_realtime_input(text=...)`, iterate `async for response in session.receive():`, and call `await session.close()` in teardown.
- **Sources (checked 2026-11-11 via official `gemini-api-docs` MCP):**
  - `https://aistudio.google.com/docs/models/gemini-3.8-live` (`gemini-api-guides/models/gemini-3.8-live.md`)
  - `https://aistudio.google.com/docs/deprecations` (`gemini-api-guides/deprecations.md#live-api-models`)
  - `https://aistudio.google.com/docs/live-api` (`gemini-api-guides/live-api.md#technical-specifications`)
  - `https://aistudio.google.com/docs/live-api/capabilities` (`gemini-api-guides/live-api/capabilities.md#audio-formats`, `#audio-transcription`, `#response-modalities`)
  - `https://aistudio.google.com/docs/live-api/get-started-sdk` (`gemini-api-guides/live-api/get-started-sdk.md`)
  - `https://aistudio.google.com/docs/live-api/live-translate` (`gemini-api-guides/live-api/live-translate.md#sending-audio`)
  - `https://aistudio.google.com/docs/live-api/live-transcribe` (`gemini-api-guides/live-api/live-transcribe.md`)



