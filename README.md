<div align="center">

# TARS
### Voice-First Multilingual Order Capture Agent for FMCG Field Sales

*Built for the Google Gemini Hackathon — "Voice-Driven Agentic Workflow Automation"*

[![Tests](https://img.shields.io/badge/pytest-184%20passed-22c55e?style=flat-square&logo=pytest&logoColor=white)](backend/tests/)
[![Type Safety](https://img.shields.io/badge/mypy%20--strict-31%20files%20clean-3b82f6?style=flat-square&logo=python&logoColor=white)](backend/app/)
[![Lint](https://img.shields.io/badge/ruff-clean-a855f7?style=flat-square)](backend/app/)
[![Frontend](https://img.shields.io/badge/vitest-7%20passed-f59e0b?style=flat-square&logo=vitest&logoColor=white)](frontend/src/)
[![Model](https://img.shields.io/badge/Gemini%20Live-gemini--3.8--live-ea580c?style=flat-square&logo=google&logoColor=white)](docs/decisions.md)
[![License](https://img.shields.io/badge/license-MIT-6b7280?style=flat-square)](LICENSE)

</div>

---

India has **12 million+ kirana (general trade) retail outlets**. Field sales reps visit them daily, capturing orders by phone, paper, or memory — a process that is slow, error-prone, and unauditable. TARS replaces it with a voice-first agent: a rep speaks an order into their mobile browser in natural, code-switched **Hinglish, Tanglish, or Marathi**, and TARS handles the rest.

**What makes TARS different from a voice assistant:** Gemini Live is an untrusted intent parser, not an autonomous actor. Every proposed action passes through a **deterministic rules firewall** before execution. Business rules enforce credit limits, discount policy, and stock levels using exact integer paise math. Orders commit to the ERP only after a verified spoken confirmation. Credit and discount breaches lock the order and route to a sales manager in real time.

<br>

## Table of Contents

- [Core Guarantees](#-core-guarantees)
- [System Architecture](#-system-architecture)
- [Tech Stack](#-tech-stack)
- [Quick Start](#-quick-start)
- [Configuration](#-configuration)
- [Running Tests](#-running-tests)
- [Demo Rehearsal](#-automated-demo-rehearsal)
- [Cloud Deployment](#-cloud-deployment-google-cloud-run)
- [Project Structure](#-project-structure)
- [The 3-Minute Demo](#-the-3-minute-demo)
- [Documentation](#-documentation)

---

## 🛡️ Core Guarantees

These are **invariants**, not guidelines. No code path may violate them.

| # | Guarantee | Enforcement |
|---|-----------|-------------|
| 1 | **LLM proposes, rules decide.** Gemini is an untrusted intent parser. | `app/rules/` — pure functions, zero I/O |
| 2 | **Zero direct LLM-to-ERP writes.** All mutations go through `repo.py`. | `app/erp/repo.py` is the only write boundary |
| 3 | **Commit requires spoken confirmation.** Orders reach `COMMITTED` only after a `CONFIRMATION_RECEIVED` audit event that follows a verified spoken read-back. | `app/agent/state.py`, `app/agent/orchestrator.py` |
| 4 | **Corrections invalidate read-back.** Any barge-in or SKU change resets `CONFIRMING → DRAFT`. | Order state machine |
| 5 | **Low confidence never guesses.** Below threshold (0.85 outlet / 0.80 SKU), TARS asks a disambiguation question. | `app/matching/outlets.py`, `app/matching/skus.py` |
| 6 | **Integer paise math throughout.** No floats for money. All amounts in `int` paise; display conversion only in the frontend. | Pydantic v2 schemas, `app/rules/` |
| 7 | **Human-in-the-loop for breaches.** `CREDIT_LIMIT_EXCEEDED` and `DISCOUNT_ABOVE_POLICY` hard-lock the order in `PENDING_APPROVAL`. | `app/approvals/service.py` |
| 8 | **Every tool call is audited.** Tool name, input, output, status, and latency (ms) are written to the trace log before the response is returned. | `app/trace/logger.py` |

---

## 🏗️ System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Mobile Browser (PWA)                         │
│  Rep Screen · Manager Queue · Live Trace Panel                      │
│  AudioWorklet → 16kHz PCM  |  WebSocket ↔  |  SSE Trace Stream    │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ WebSocket / REST
┌──────────────────────────▼──────────────────────────────────────────┐
│                     FastAPI Backend (Uvicorn)                       │
│                                                                     │
│  ┌─────────────────────┐    ┌──────────────────────────────────┐   │
│  │   GeminiLiveBridge  │    │       REST API Routers           │   │
│  │  (bidirectional     │    │  /api/sessions  /api/approvals   │   │
│  │   audio framing)    │    │  /api/trace     /api/catalog     │   │
│  └──────────┬──────────┘    └──────────────────────────────────┘   │
│             │ tool call events                                      │
│  ┌──────────▼──────────────────────────────────────────────────┐   │
│  │                   OrderOrchestrator                          │   │
│  │  dispatch_tool() → TraceLogger → Rules Engine → repo.py     │   │
│  │                                                              │   │
│  │  ┌────────────────┐  ┌──────────────┐  ┌────────────────┐  │   │
│  │  │ State Machine  │  │ Rules Engine │  │ Fuzzy Matching │  │   │
│  │  │ state.py       │  │ credit.py    │  │ outlets.py     │  │   │
│  │  │ 5 states       │  │ discount.py  │  │ skus.py        │  │   │
│  │  │ 8 transitions  │  │ stock.py     │  │ line_parser.py │  │   │
│  │  └────────────────┘  └──────────────┘  └────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│             │                                                       │
│  ┌──────────▼──────────────────────────────────────────────────┐   │
│  │                 Mock ERP (SQLAlchemy + SQLite)               │   │
│  │  Outlets · SKUs · Schemes · Orders · Lines · Approvals ·    │   │
│  │  Invoices · Audit Log · Outlet Aliases · SKU Aliases        │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                           │ v1beta Live API
┌──────────────────────────▼──────────────────────────────────────────┐
│              Google Gemini Live (gemini-3.8-live)                   │
│  16kHz PCM in · 24kHz PCM out · output_audio_transcription         │
│  8 bounded tools · Hinglish / Tanglish / Marathi system prompt     │
└─────────────────────────────────────────────────────────────────────┘
```

### Order State Machine

```
[*] ──resolve_outlet──▶ DRAFT ──add/update/remove lines──▶ DRAFT
                          │                                   ▲
                          │ rules pass                        │ correction / barge-in
                          ▼                                   │
                      CONFIRMING ──────────────────────────────
                          │
                   spoken "haan" / "confirm"
                          │
                          ▼
                      COMMITTED ──▶ Invoice created
                          
DRAFT / CONFIRMING ──breach──▶ PENDING_APPROVAL ──manager approves──▶ CONFIRMING
                                                  ──manager rejects──▶ REJECTED
DRAFT / CONFIRMING ──cancel──▶ REJECTED
```

---

## 🛠️ Tech Stack

| Layer | Technology | Why |
|---|---|---|
| **Voice AI** | Google GenAI SDK · `gemini-3.8-live` | Native bidirectional audio; no STT/TTS round-trips |
| **Audio Pipeline** | Web Audio API `AudioWorklet` · 16kHz PCM in / 24kHz PCM out | ADC-compliant, low-latency mic capture without transcoding |
| **Backend** | Python 3.12 · FastAPI · Uvicorn · WebSockets · SSE | Async-native; single process handles WS, REST, and SSE concurrently |
| **Rules Engine** | Pure Python functions · Pydantic v2 DTOs · `int` paise | Deterministic, testable, zero side-effects |
| **Fuzzy Matching** | RapidFuzz `token_set_ratio` · winner-gap confidence scoring | Handles Hinglish phonetics, abbreviations, and code-switching |
| **Mock ERP** | SQLAlchemy 2.0 · SQLite · `StaticPool` for tests | Fast reset, normalized schema, Pydantic boundary layer |
| **Frontend** | React 19 · TypeScript · Vite · CSS custom properties | Dark industrial UI; 3-panel command centre + mobile PWA layout |
| **Quality** | `pytest` · `mypy --strict` · `ruff` · `vitest` · Playwright | 184 backend + 7 frontend tests; zero type errors |
| **Deploy** | Docker Compose (local) · Google Cloud Run (production) | WebSocket session affinity; Secret Manager for API key |

---

## ⚡ Quick Start

### Option A — Docker Compose (recommended)

```bash
# 1. Copy the environment template
cp .env.example .env

# 2. Set your Gemini API key
#    Open .env and set: TARS_GEMINI_API_KEY=AIza...

# 3. Start all services
docker compose up --build
```

| Service | URL |
|---|---|
| Rep / Manager / Trace UI | http://localhost:5173 |
| Backend health check | http://localhost:8000/health |
| Interactive API docs | http://localhost:8000/docs |

---

### Option B — Local Development

#### Backend (Python 3.12)

```bash
cd backend

# Create and activate virtual environment
python -m venv .venv

# Windows
.\.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"

# Seed the mock ERP with synthetic FMCG data
python -m app.erp.seed

# Start the API server (hot-reload)
uvicorn app.main:app --reload --port 8000
```

#### Frontend (Node 20+)

```bash
cd frontend
npm install
npm run dev        # → http://localhost:5173
```

---

## ⚙️ Configuration

All settings are read from `.env` via `pydantic-settings` with the `TARS_` prefix. No secrets in code.

| Variable | Default | Description |
|---|---|---|
| `TARS_GEMINI_API_KEY` | *(required)* | Google AI API key |
| `TARS_GEMINI_MODEL` | `gemini-3.8-live` | Gemini Live model identifier |
| `TARS_DATABASE_URL` | `sqlite:///./tars.db` | SQLAlchemy database URL |
| `TARS_OUTLET_MATCH_THRESHOLD` | `0.85` | Minimum score to auto-accept outlet match |
| `TARS_SKU_MATCH_THRESHOLD` | `0.80` | Minimum score to auto-accept SKU match |
| `VITE_API_URL` | *(required for frontend)* | Backend base URL for the React app |

Copy `.env.example` as your starting point — it lists every variable with placeholder values and no real secrets.

---

## 🧪 Running Tests

The full quality gate covers 184 backend tests, strict type checking across 31 source files, and 7 frontend tests.

```bash
# ── Backend ───────────────────────────────────────────────────────────────

cd backend

# 1. All 184 unit, integration, and scenario tests
.venv/Scripts/python.exe -m pytest tests -v        # Windows
python -m pytest tests -v                           # macOS / Linux

# 2. Strict static type verification (zero errors required)
python -m mypy --strict \
  app/trace app/approvals app/agent app/voice \
  app/rules app/erp app/matching \
  app/config.py app/main.py

# 3. Linter
python -m ruff check app/ tests/ scripts/

# ── Frontend ──────────────────────────────────────────────────────────────

cd frontend

# 4. Component and integration tests
npm test -- --run

# 5. TypeScript compilation + production build
npm run build
```

### Test Coverage by Module

| Test File | Tests | What It Covers |
|---|---|---|
| `test_health.py` | 1 | FastAPI health endpoint |
| `test_seed_data.py` | 3 | CSV data integrity (outlets, SKUs, schemes) |
| `test_models.py` | 3 | SQLAlchemy model creation and alias normalization |
| `test_repo.py` | 12 | ERP repository layer (all mutations via `repo.py`) |
| `test_outlets_matching.py` | 11 | RapidFuzz outlet matching and confidence scoring |
| `test_line_parser.py` | 30 | Hinglish / Tanglish / Marathi order fragment parsing |
| `test_sku_matching.py` | 13 | SKU catalog resolution with pack disambiguation |
| `test_rules.py` | 26 | Credit, discount, and stock business rules |
| `test_state_machine.py` | 12 | Order state machine transitions and guards |
| `test_config.py` | 4 | Environment variable loading and defaults |
| `test_voice_bridge.py` | 8 | Gemini Live audio bridge, barge-in, TTFA logging |
| `test_tools_and_orchestrator.py` | 7 | End-to-end tool dispatch, approval flow, commit |
| `test_api_endpoints.py` | 15 | FastAPI REST endpoints (StaticPool in-memory DB) |
| `test_resilience_and_languages.py` | 37 | Multilingual, noisy audio, and exception scenarios |
| `test_demo_rehearsal.py` | 2 | Automated double-pass demo rehearsal |
| **Total** | **184** | |

---

## 🎭 Automated Demo Rehearsal

TARS ships an end-to-end rehearsal runner that executes the complete 4-scene demo flow — without a live microphone or manual typing — and reports timing and invoice accuracy.

```bash
cd backend
python scripts/rehearse_demo.py
```

**Measured performance (two consecutive runs):**

| Metric | Run 1 | Run 2 |
|---|---|---|
| Total elapsed | ~414 ms | ~385 ms |
| Tool calls audited | 12 | 12 |
| Median tool latency | 13 ms | 9 ms |
| Invoice generated | `INV-000001` ✓ | `INV-000001` ✓ |

The rehearsal covers: outlet resolution → line items → scheme discount → credit breach + manager approval → spoken read-back gate → spoken confirmation → ERP commit → invoice.

---

## ☁️ Cloud Deployment (Google Cloud Run)

Deployment artifacts are in `deploy/`. Cloud Run is pre-configured for WebSocket session affinity and Secret Manager integration.

```powershell
# Windows PowerShell
.\deploy\deploy_cloudrun.ps1 -ProjectId "your-gcp-project" -Region "asia-south1"
```

```bash
# macOS / Linux
./deploy/deploy_cloudrun.sh "your-gcp-project" "asia-south1"
```

The deployment script:
1. Builds and pushes the backend container to Artifact Registry.
2. Creates or updates the Cloud Run service from `deploy/cloudrun-service.yaml`.
3. Sets `TARS_GEMINI_API_KEY` from Secret Manager (`tars-gemini-api-key`).
4. Enables WebSocket session affinity (`run.googleapis.com/sessionAffinity: "true"`).
5. Binds dynamic `$PORT` from Cloud Run's environment.

---

## 📂 Project Structure

```
TARS/
├── backend/
│   ├── app/
│   │   ├── agent/
│   │   │   ├── state.py          # Order state machine (5 states, 8 transitions, commit gate)
│   │   │   ├── tools.py          # 8 Pydantic v2 tool schemas exposed to Gemini
│   │   │   └── orchestrator.py   # Tool dispatch with trace logging and rules validation
│   │   ├── approvals/
│   │   │   └── service.py        # Human-in-the-loop approval request lifecycle
│   │   ├── erp/
│   │   │   ├── models.py         # SQLAlchemy 2.0 Mapped models (10 tables)
│   │   │   ├── schemas.py        # Pydantic v2 DTOs (all money as int paise)
│   │   │   ├── repo.py           # Sole ERP write boundary — LLM never touches this directly
│   │   │   └── seed.py           # Idempotent seed from data/*.csv
│   │   ├── matching/
│   │   │   ├── outlets.py        # RapidFuzz outlet resolution with confidence scoring
│   │   │   ├── skus.py           # SKU catalog resolution with pack disambiguation
│   │   │   └── line_parser.py    # Hinglish / Tanglish / Marathi order fragment parser
│   │   ├── rules/
│   │   │   ├── credit.py         # Credit limit evaluation (pure function, int paise)
│   │   │   ├── discount.py       # Discount policy evaluation with scheme upsell logic
│   │   │   └── stock.py          # Stock availability and free-pack checks
│   │   ├── trace/
│   │   │   ├── logger.py         # Structured audit logger (TraceEntry Pydantic model)
│   │   │   └── stream.py         # In-memory SSE hub for live trace feed
│   │   ├── voice/
│   │   │   ├── audio.py          # PCM framing, sample rate constants (ADR-004)
│   │   │   ├── gemini_live.py    # Bidirectional Gemini Live bridge, barge-in, TTFA
│   │   │   └── prompts.py        # Hinglish/multilingual system instruction
│   │   ├── config.py             # Pydantic Settings (TARS_* prefix, .env)
│   │   └── main.py               # FastAPI app, all REST + WebSocket endpoints
│   ├── scripts/
│   │   ├── live_smoke.py         # Gemini Live connectivity probe (TTFA measurement)
│   │   ├── rehearse_demo.py      # Automated 4-scene double-pass demo rehearsal
│   │   └── generate_audio_fixtures.py  # Synthetic 16kHz WAV fixture generator
│   └── tests/                    # 184 tests across 15 test files
│
├── frontend/
│   └── src/
│       ├── rep/RepScreen.tsx     # Voice capture, transcript feed, draft order, invoice banner
│       ├── manager/ManagerQueue.tsx  # Approval queue with reason badges and decide buttons
│       ├── trace/TracePanel.tsx  # Real-time tool call log with KPI bar
│       ├── App.tsx               # 3-tab shell (Rep · Manager · Trace) with health indicator
│       ├── types.ts              # TypeScript interfaces mirroring backend Pydantic DTOs
│       └── index.css             # Dark carbon-slate design system with CSS custom properties
│
├── data/
│   ├── outlets.csv               # 30 synthetic kirana outlets with Hinglish aliases
│   ├── catalog.csv               # 80 FMCG SKUs across 10 invented brands
│   └── schemes.csv               # 10 trade promotion schemes (quantity-linked discounts)
│
├── deploy/
│   ├── cloudrun-service.yaml     # Knative service manifest (session affinity, Secret Manager)
│   ├── deploy_cloudrun.ps1       # Windows PowerShell deployment script
│   └── deploy_cloudrun.sh        # Bash deployment script
│
├── demo/
│   ├── script.md                 # 3-minute timed live demo script (4 scenes)
│   ├── test_utterances.md        # Hinglish / Tanglish / Marathi test utterances and results
│   └── recordings/               # Synthetic 16kHz mono PCM WAV fixtures
│
├── docs/
│   ├── architecture.md           # System diagrams: audio pipeline, state machine, ER diagram
│   ├── commercial_case.md        # FMCG unit economics and 42.8× ROI analysis
│   ├── decisions.md              # Architecture Decision Records (ADR-001 through ADR-004)
│   └── submission_checklist.md   # Hackathon submission package
│
├── docker-compose.yml            # Backend (8000) + Frontend (5173) with shared .env
├── .env.example                  # All variables documented, no real secrets
└── AGENTS.md                     # AI agent working rules and architectural contracts
```

---

## 🎬 The 3-Minute Demo

The live demo covers four scenes, each testing a distinct system behaviour:

| Scene | What happens | System under test |
|---|---|---|
| **1 — Normal order** | Rep speaks "Tiwari ji ke liye 5 carton Glucose biscuit aur do bag atta" | Outlet resolution · line parsing · scheme discount · read-back · spoken confirm → `COMMITTED` |
| **2 — Spoken correction** | Rep says "nahin, teen carton" mid-session | Barge-in handling · `CONFIRMING → DRAFT` reset · re-read-back |
| **3 — Credit breach** | Order pushes outlet over credit limit | `PENDING_APPROVAL` lock · manager queue notification · approval → `CONFIRMING` |
| **4 — Multilingual** | Same order in Tanglish ("rendu bag atta kudunga") | Tamil/Marathi number words · filler stripping · 100% match accuracy |

Run the automated rehearsal to verify the full flow end-to-end before the live presentation:

```bash
cd backend && python scripts/rehearse_demo.py
```

---

## 📚 Documentation

| Document | Contents |
|---|---|
| [Architecture & Data Model](docs/architecture.md) | High-level flowchart, audio pipeline, deterministic tool boundary diagram, order state machine (`stateDiagram-v2`), ERP entity-relationship diagram |
| [Commercial Case](docs/commercial_case.md) | FMCG distribution problem sizing, unit economics in ₹, 42.8× measured ROI per rep, explicit `[ASSUMPTION]` / `[MEASURED IN REPO]` labels throughout |
| [Architecture Decision Records](docs/decisions.md) | ADR-001 (alias normalization), ADR-002/004 (Gemini Live model confirmation), ADR-003 (PWA service worker deferral) |
| [3-Minute Demo Script](demo/script.md) | Timed cue-by-cue script for the live hackathon presentation |
| [Multilingual Test Utterances](demo/test_utterances.md) | 100% accuracy results for Hinglish, Tanglish, and Marathi order fragments |

---

## 📄 License

MIT — see [LICENSE](LICENSE).

---

<div align="center">

Built with [Gemini Live](https://aistudio.google.com/docs/live-api/overview) · [FastAPI](https://fastapi.tiangolo.com/) · [React](https://react.dev/)

*TARS uses only synthetic data. All outlet names, owner names, and brand names are invented and do not represent real businesses or individuals.*

---

**Author:** Tushar Singh

</div>
