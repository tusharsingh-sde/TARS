# TARS — System Architecture & Data Model

## 1. High-Level System Architecture

```mermaid
flowchart TB
    subgraph Client["Frontend PWA (React 19 + TypeScript + Vite)"]
        RepUI["Rep Screen (P5-01)\nAudioWorklet 16kHz In / 24kHz Out\nLive Transcript + Draft Order"]
        MgrUI["Manager Queue (P5-02)\nPending Approvals\nOne-Tap Approve / Reject"]
        TraceUI["Live Action Trace (P5-03)\nPer-Tool Latency (ms)\nStatus: OK | BLOCKED | ERROR"]
    end

    subgraph FastAPI["FastAPI Backend (backend/app/main.py)"]
        WS["WS /ws/voice/{session_id}\nREST /api/sessions/{id}/tool"]
        Bridge["GeminiLiveBridge (app/voice/gemini_live.py)\ngemini-3.8-live (v1beta)\nBarge-In & TTFA Metrics"]
        Orch["OrderOrchestrator (app/agent/orchestrator.py)\nDeterministic Tool Dispatch & Latency Timer"]
        ApproveSvc["Approval Service (app/approvals/service.py)"]
        TraceHub["TraceStreamHub (app/trace/stream.py)\nSSE /api/trace/stream"]
    end

    subgraph Core["Deterministic Domain Layer (Pure Functions)"]
        Match["Matching Engine (app/matching/)\nline_parser.py (Hinglish/Tanglish/Marathi)\noutlets.py & skus.py (RapidFuzz)"]
        Rules["Business Rules (app/rules/)\ncredit.py | discount.py | stock.py\n(Pure Integer Paise & BPS)"]
        State["Order State Machine (app/agent/state.py)\nAllowed Transitions + Read-Back Gate"]
    end

    subgraph Storage["Mock ERP Persistence (app/erp/)"]
        Repo["ERP Repository (repo.py)"]
        DB[("SQLite DB (tars.db)\n10 Normalized Tables")]
    end

    RepUI <-->|"PCM16 Audio / JSON Events"| WS
    WS <--> Bridge
    Bridge <-->|"Tool Calls / Responses"| Orch
    WS --> Orch
    Orch --> Match
    Orch --> Rules
    Orch --> State
    Orch --> ApproveSvc
    Orch --> Repo
    Orch --> TraceHub
    MgrUI <-->|"GET /api/approvals\nPOST /api/approvals/{id}/decide"| ApproveSvc
    ApproveSvc --> State
    ApproveSvc --> Repo
    TraceHub -->|"GET /api/trace\nSSE /api/trace/stream"| TraceUI
    Repo --> DB
```

---

## 2. Audio Framing & Voice Streaming Pipeline

TARS uses native, bidirectional streaming audio powered by the **Gemini Live API** (`gemini-3.8-live` on `v1beta`).

```mermaid
flowchart LR
    Mic["User Microphone\n(Browser Web Audio)"] -->|"PCM 16-bit 16kHz\n100ms frames (3,200 B)"| AudioWorkletIn["AudioWorklet Processor\n(Linear Resampler & Framer)"]
    AudioWorkletIn -->|"Binary WS Frames"| WS["FastAPI WebSocket\n/ws/voice/{session_id}"]
    WS -->|"validate_pcm16_frame\nchunk_pcm16 (ADR-004)"| Bridge["GeminiLiveBridge\n(backend/app/voice/gemini_live.py)"]
    Bridge -->|"realtime_input\naudio/pcm;rate=16000"| Gemini["Google Gemini Live API\n(gemini-3.8-live)"]
    Gemini -->|"server_content\naudio/pcm;rate=24000"| Bridge
    Bridge -->|"Binary WS Frames"| AudioWorkletOut["AudioWorklet Player\n(Jitter Buffer & 24kHz DAC)"]
    AudioWorkletOut --> Speaker["Device Speaker / Earpiece"]
```

### Key Audio Invariants:
1. **Input Stream:** Linear 16-bit PCM, single-channel mono, sampled at 16,000 Hz (`audio/pcm;rate=16000`). Framed into aligned chunks (multiples of 2 bytes, 100ms default chunks = 3,200 bytes).
2. **Output Stream:** Linear 16-bit PCM, single-channel mono, sampled at 24,000 Hz (`audio/pcm;rate=24000`).
3. **Barge-In (Interruption) Detection:** When the user begins speaking while the model is playing synthesized audio, the Gemini Live server emits `interrupted=True`. The bridge catches this event, marks the active turn interrupted, resets the read-back confirmation status (`read_back_spoken = False`), and transitions the order state machine back from `CONFIRMING` to `DRAFT`.
4. **Time-to-First-Audio (TTFA) Tracking:** Every speech turn and post-tool turn computes high-resolution latency from the final inbound input chunk until the first outbound PCM chunk is yielded (`[MEASURED IN REPO]: 455.6 ms`).

---

## 3. Deterministic Tool Boundary & Safety

Gemini operates in a strictly sandboxed, bounded tool environment. The LLM never has access to arbitrary SQL execution, network sockets, or ERP mutation endpoints.

The LLM is configured with **8 strict tool declarations** via `backend/app/agent/tools.py`:
1. `resolve_outlet(outlet_query: str)`: Resolves kirana store against registered distributor beat. Returns `MATCHED`, `AMBIGUOUS`, or `NOT_FOUND`.
2. `resolve_sku(product_text: str, pack_size: str | None = None)`: Resolves product and pack size against 80+ catalog items.
3. `add_or_update_line(sku_id: str, quantity: int, unit: str | None = None)`: Validates stock availability and applies best active trade schemes.
4. `remove_or_correct_line(sku_id: str, new_quantity: int = 0)`: Adjusts or deletes an existing order line and recalculates scheme qualifications.
5. `check_order_rules(outlet_id: str | None = None)`: Pure evaluation of credit headroom and discount policy caps.
6. `record_read_back(order_id: str, read_back_text: str)`: Records that the voice agent completed speaking the line items, quantities, and grand total.
7. `request_manager_approval(order_id: str, reason: str, notes: str | None = None)`: Places an order into `PENDING_APPROVAL` and notifies the sales manager.
8. `commit_order(order_id: str, confirmation_phrase: str)`: Commits order to ERP and creates official invoice. Blocked unless spoken read-back is audited.

---

## 4. Order State Machine

Implemented in `backend/app/agent/state.py`. Governs the complete lifecycle of field sales orders.

```mermaid
stateDiagram-v2
    [*] --> DRAFT: resolve_outlet (MATCHED)
    DRAFT --> DRAFT: add_or_update_line / remove_or_correct_line
    DRAFT --> PENDING_APPROVAL: request_manager_approval\n(CREDIT_LIMIT_EXCEEDED / DISCOUNT_ABOVE_POLICY)
    DRAFT --> CONFIRMING: record_read_back\n(No active breaches)
    DRAFT --> REJECTED: cancel_order

    PENDING_APPROVAL --> CONFIRMING: Manager Approves\n(resolve_approval_request approved=True)
    PENDING_APPROVAL --> REJECTED: Manager Rejects\n(resolve_approval_request approved=False)

    CONFIRMING --> DRAFT: Spoken Correction / Barge-In\n(add_or_update_line / remove_or_correct_line\nclears read_back_spoken)
    CONFIRMING --> COMMITTED: commit_order\n(Requires read_back_spoken=True\n& CONFIRMATION_RECEIVED audit event)
    CONFIRMING --> REJECTED: cancel_order

    COMMITTED --> [*]
    REJECTED --> [*]
```

### State Machine Transition Rules:
- `DRAFT`: Initial state created upon resolving a valid outlet. Rep speaks SKUs and quantities. Line updates keep order in `DRAFT`.
- `CONFIRMING`: Entered only when `record_read_back` is called with no active credit or discount breaches.
- `Spoken Correction Reset`: Any modification (`add_or_update_line`, `remove_or_correct_line`) while in `CONFIRMING` resets status back to `DRAFT` and clears `read_back_spoken`.
- `COMMITTED`: Can ONLY be reached from `CONFIRMING` via `commit_order` when `read_back_spoken == True` and an explicit `CONFIRMATION_RECEIVED` audit entry exists.
- `PENDING_APPROVAL`: Hard lock entered when credit limit or discount cap is breached. No commitment can occur until a manager reviews the approval queue.
- `REJECTED`: Terminal state if cancelled by rep or rejected by manager.

---

## 5. Database Schema & Entity Relationships

The mock ERP schema is implemented with **SQLAlchemy 2.0** on SQLite (`backend/app/erp/models.py`), utilizing 10 fully normalized tables.

```mermaid
erDiagram
    OUTLET ||--o{ OUTLET_ALIAS : has
    OUTLET ||--o{ ORDER : places
    OUTLET ||--o{ INVOICE : billed
    
    CATALOG_ITEM ||--o{ SKU_ALIAS : has
    CATALOG_ITEM ||--o{ ORDER_LINE : contains
    CATALOG_ITEM ||--o{ SCHEME : qualifies
    
    ORDER ||--|{ ORDER_LINE : includes
    ORDER ||--o| INVOICE : generates
    ORDER ||--o{ APPROVAL_REQUEST : triggers
    ORDER ||--o{ AUDIT_LOG : tracks
    
    OUTLET {
        string id PK
        string name
        string owner_name
        string phone
        string address
        string beat
        int credit_limit_paise
        int outstanding_paise
        int overdue_paise
        boolean is_active
    }

    OUTLET_ALIAS {
        int id PK
        string outlet_id FK
        string alias
    }

    CATALOG_ITEM {
        string sku_id PK
        string brand
        string product_name
        string variant
        string pack_size
        string category
        int mrp_paise
        int base_price_paise
        int tax_rate_bps
        int inventory_qty
    }

    SKU_ALIAS {
        int id PK
        string sku_id FK
        string alias
    }

    SCHEME {
        string scheme_id PK
        string sku_id FK
        string scheme_type
        int min_qty
        int discount_bps
        date start_date
        date end_date
    }

    ORDER {
        string order_id PK
        string session_id
        string outlet_id FK
        string rep_id
        string status
        int subtotal_paise
        int discount_paise
        int tax_paise
        int grand_total_paise
        boolean read_back_spoken
    }

    ORDER_LINE {
        int id PK
        string order_id FK
        string sku_id FK
        int quantity
        string unit
        int unit_price_paise
        int discount_bps
        int line_total_paise
    }

    INVOICE {
        string invoice_id PK
        string order_id FK
        string outlet_id FK
        int total_paise
        datetime issued_at
    }

    APPROVAL_REQUEST {
        string approval_id PK
        string order_id FK
        string rule_name
        string reason
        int excess_amount_paise
        string status
        string decided_by
        datetime decided_at
    }

    AUDIT_LOG {
        int id PK
        string order_id FK
        string event_type
        string payload_json
        datetime created_at
    }
```

---

## 6. Non-Negotiable Architecture Rules & Invariants

From `AGENTS.md` §2 and §4:

1. **The LLM proposes, the rules decide:** Gemini may call tools to propose actions. Only the pure business rules engine (`rules/{credit,discount,stock}.py`) and state machine (`agent/state.py`) decide whether an action executes.
2. **The LLM never writes to the ERP directly:** All mutations go through `backend/app/erp/repo.py` invoked strictly by `OrderOrchestrator` after rules validation.
3. **Commit requires spoken confirmation:** An order may move to `COMMITTED` only after a `CONFIRMATION_RECEIVED` audit event is recorded, following a verified `READ_BACK_SPOKEN` turn.
4. **Every tool call is logged:** Every single tool call records `tool_name`, `input_payload`, `output_payload`, `status` (`OK`, `BLOCKED`, `ERROR`), and `latency_ms` in `TraceStreamHub`.
5. **Low confidence never guesses:** If matching confidence is below `0.85` (outlets) or `0.80` (SKUs), or the gap between top-1 and top-2 candidates is `< 0.05`, the agent issues a disambiguation prompt. It NEVER silently guesses.
6. **Pure integer math:** All monetary calculations use integer `paise` (1 ₹ = 100 paise) and basis points (1% = 100 bps). Floating-point math is strictly forbidden in financial calculations.
7. **Human-in-the-loop approvals:** Credit limit breaches and discount cap exceptions generate an `APPROVAL_REQUEST` in `PENDING_APPROVAL`. Only a sales manager endpoint can approve or reject.
```

---

## 2. Audio Framing & Voice Streaming Pipeline

TARS uses native, bidirectional streaming audio powered by the **Gemini Live API** (`gemini-3.8-live` on `v1beta`).

```mermaid
flowchart LR
    Mic["User Microphone\n(Browser Web Audio)"] -->|"PCM 16-bit 16kHz\n100ms frames (3,200 B)"| AudioWorkletIn["AudioWorklet Processor\n(Linear Resampler & Framer)"]
    AudioWorkletIn -->|"Binary WS Frames"| WS["FastAPI WebSocket\n/ws/voice/{session_id}"]
    WS -->|"validate_pcm16_frame\nchunk_pcm16 (ADR-004)"| Bridge["GeminiLiveBridge\n(backend/app/voice/gemini_live.py)"]
    Bridge -->|"realtime_input\naudio/pcm;rate=16000"| Gemini["Google Gemini Live API\n(gemini-3.8-live)"]
    Gemini -->|"server_content\naudio/pcm;rate=24000"| Bridge
    Bridge -->|"Binary WS Frames"| AudioWorkletOut["AudioWorklet Player\n(Jitter Buffer & 24kHz DAC)"]
    AudioWorkletOut --> Speaker["Device Speaker / Earpiece"]
```

### Key Audio Invariants:
1. **Input Stream:** Linear 16-bit PCM, single-channel mono, sampled at 16,000 Hz (`audio/pcm;rate=16000`). Framed into aligned chunks (multiples of 2 bytes, 100ms default chunks = 3,200 bytes).
2. **Output Stream:** Linear 16-bit PCM, single-channel mono, sampled at 24,000 Hz (`audio/pcm;rate=24000`).
3. **Barge-In (Interruption) Detection:** When the user begins speaking while the model is playing synthesized audio, the Gemini Live server emits `interrupted=True`. The bridge catches this event, marks the active turn interrupted, resets the read-back confirmation status (`read_back_spoken = False`), and transitions the order state machine back from `CONFIRMING` to `DRAFT`.
4. **Time-to-First-Audio (TTFA) Tracking:** Every speech turn and post-tool turn computes high-resolution latency from the final inbound input chunk until the first outbound PCM chunk is yielded (`[MEASURED IN REPO]: 455.6 ms`).

---

## 3. Deterministic Tool Boundary & Safety

Gemini operates in a strictly sandboxed, bounded tool environment. The LLM never has access to arbitrary SQL execution, network sockets, or ERP mutation endpoints.

The LLM is configured with **8 strict tool declarations** via `backend/app/agent/tools.py`:
1. `resolve_outlet(outlet_query: str)`: Resolves kirana store against registered distributor beat. Returns `MATCHED`, `AMBIGUOUS`, or `NOT_FOUND`.
2. `resolve_sku(product_text: str, pack_size: str | None = None)`: Resolves product and pack size against 80+ catalog items.
3. `add_or_update_line(sku_id: str, quantity: int, unit: str | None = None)`: Validates stock availability and applies best active trade schemes.
4. `remove_or_correct_line(sku_id: str, new_quantity: int = 0)`: Adjusts or deletes an existing order line and recalculates scheme qualifications.
5. `check_order_rules(outlet_id: str | None = None)`: Pure evaluation of credit headroom and discount policy caps.
6. `record_read_back(order_id: str, read_back_text: str)`: Records that the voice agent completed speaking the line items, quantities, and grand total.
7. `request_manager_approval(order_id: str, reason: str, notes: str | None = None)`: Places an order into `PENDING_APPROVAL` and notifies the sales manager.
8. `commit_order(order_id: str, confirmation_phrase: str)`: Commits order to ERP and creates official invoice. Blocked unless spoken read-back is audited.

---

## 4. Order State Machine

Implemented in `backend/app/agent/state.py`. Governs the complete lifecycle of field sales orders.

```mermaid
stateDiagram-v2
    [*] --> DRAFT: resolve_outlet (MATCHED)
    DRAFT --> DRAFT: add_or_update_line / remove_or_correct_line
    DRAFT --> PENDING_APPROVAL: request_manager_approval\n(CREDIT_LIMIT_EXCEEDED / DISCOUNT_ABOVE_POLICY)
    DRAFT --> CONFIRMING: record_read_back\n(No active breaches)
    DRAFT --> REJECTED: cancel_order

    PENDING_APPROVAL --> CONFIRMING: Manager Approves\n(resolve_approval_request approved=True)
    PENDING_APPROVAL --> REJECTED: Manager Rejects\n(resolve_approval_request approved=False)

    CONFIRMING --> DRAFT: Spoken Correction / Barge-In\n(add_or_update_line / remove_or_correct_line\nclears read_back_spoken)
    CONFIRMING --> COMMITTED: commit_order\n(Requires read_back_spoken=True\n& CONFIRMATION_RECEIVED audit event)
    CONFIRMING --> REJECTED: cancel_order

    COMMITTED --> [*]
    REJECTED --> [*]
```

### State Machine Transition Rules:
- `DRAFT`: Initial state created upon resolving a valid outlet. Rep speaks SKUs and quantities. Line updates keep order in `DRAFT`.
- `CONFIRMING`: Entered only when `record_read_back` is called with no active credit or discount breaches.
- `Spoken Correction Reset`: Any modification (`add_or_update_line`, `remove_or_correct_line`) while in `CONFIRMING` resets status back to `DRAFT` and clears `read_back_spoken`.
- `COMMITTED`: Can ONLY be reached from `CONFIRMING` via `commit_order` when `read_back_spoken == True` and an explicit `CONFIRMATION_RECEIVED` audit entry exists.
- `PENDING_APPROVAL`: Hard lock entered when credit limit or discount cap is breached. No commitment can occur until a manager reviews the approval queue.
- `REJECTED`: Terminal state if cancelled by rep or rejected by manager.

---

## 5. Database Schema & Entity Relationships

The mock ERP schema is implemented with **SQLAlchemy 2.0** on SQLite (`backend/app/erp/models.py`), utilizing 10 fully normalized tables.

```mermaid
erDiagram
    OUTLET ||--o{ OUTLET_ALIAS : has
    OUTLET ||--o{ ORDER : places
    OUTLET ||--o{ INVOICE : billed
    
    CATALOG_ITEM ||--o{ SKU_ALIAS : has
    CATALOG_ITEM ||--o{ ORDER_LINE : contains
    CATALOG_ITEM ||--o{ SCHEME : qualifies
    
    ORDER ||--|{ ORDER_LINE : includes
    ORDER ||--o| INVOICE : generates
    ORDER ||--o{ APPROVAL_REQUEST : triggers
    ORDER ||--o{ AUDIT_LOG : tracks
    
    OUTLET {
        string id PK
        string name
        string owner_name
        string phone
        string address
        string beat
        int credit_limit_paise
        int outstanding_paise
        int overdue_paise
        boolean is_active
    }

    OUTLET_ALIAS {
        int id PK
        string outlet_id FK
        string alias
    }

    CATALOG_ITEM {
        string sku_id PK
        string brand
        string product_name
        string variant
        string pack_size
        string category
        int mrp_paise
        int base_price_paise
        int tax_rate_bps
        int inventory_qty
    }

    SKU_ALIAS {
        int id PK
        string sku_id FK
        string alias
    }

    SCHEME {
        string scheme_id PK
        string sku_id FK
        string scheme_type
        int min_qty
        int discount_bps
        date start_date
        date end_date
    }

    ORDER {
        string order_id PK
        string session_id
        string outlet_id FK
        string rep_id
        string status
        int subtotal_paise
        int discount_paise
        int tax_paise
        int grand_total_paise
        boolean read_back_spoken
    }

    ORDER_LINE {
        int id PK
        string order_id FK
        string sku_id FK
        int quantity
        string unit
        int unit_price_paise
        int discount_bps
        int line_total_paise
    }

    INVOICE {
        string invoice_id PK
        string order_id FK
        string outlet_id FK
        int total_paise
        datetime issued_at
    }

    APPROVAL_REQUEST {
        string approval_id PK
        string order_id FK
        string rule_name
        string reason
        int excess_amount_paise
        string status
        string decided_by
        datetime decided_at
    }

    AUDIT_LOG {
        int id PK
        string order_id FK
        string event_type
        string payload_json
        datetime created_at
    }
```

---

## 6. Non-Negotiable Architecture Rules & Invariants

From `AGENTS.md` §2 and §4:

1. **The LLM proposes, the rules decide:** Gemini may call tools to propose actions. Only the pure business rules engine (`rules/{credit,discount,stock}.py`) and state machine (`agent/state.py`) decide whether an action executes.
2. **The LLM never writes to the ERP directly:** All mutations go through `backend/app/erp/repo.py` invoked strictly by `OrderOrchestrator` after rules validation.
3. **Commit requires spoken confirmation:** An order may move to `COMMITTED` only after a `CONFIRMATION_RECEIVED` audit event is recorded, following a verified `READ_BACK_SPOKEN` turn.
4. **Every tool call is logged:** Every single tool call records `tool_name`, `input_payload`, `output_payload`, `status` (`OK`, `BLOCKED`, `ERROR`), and `latency_ms` in `TraceStreamHub`.
5. **Low confidence never guesses:** If matching confidence is below `0.85` (outlets) or `0.80` (SKUs), or the gap between top-1 and top-2 candidates is `< 0.05`, the agent issues a disambiguation prompt. It NEVER silently guesses.
6. **Pure integer math:** All monetary calculations use integer `paise` (1 ₹ = 100 paise) and basis points (1% = 100 bps). Floating-point math is strictly forbidden in financial calculations.
7. **Human-in-the-loop approvals:** Credit limit breaches and discount cap exceptions generate an `APPROVAL_REQUEST` in `PENDING_APPROVAL`. Only a sales manager endpoint can approve or reject.
