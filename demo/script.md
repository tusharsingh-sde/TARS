# TARS: Three-Minute Live Hackathon Demo Script

**Project:** TARS — Voice-First FMCG Field Sales Order Agent  
**Target Duration:** Exactly 3 minutes (0:00 to 3:00)  
**Persona:** Ravi Kumar, Field Sales Representative (Meerut Route R1, Uttar Pradesh)  
**Demo Environment:** Noisy market ambient audio, phone browser (PWA on Android viewport), live backend with Gemini Live API + Mock ERP + Action Trace panel.

---

## Timing & Scene Breakdown

### 0:00 – 0:45 | Scene 1: Noisy Kirana Order & Trade Scheme Application
- **Visual:** Rep opens TARS PWA on phone. Beat `R1` is active. Rep screen shows the big mic button and live transcript stream.
- **Action:** Rep taps the microphone and speaks in natural Hinglish against shop chatter:
  > **Ravi:** *"Tiwari ji ke yahan order likho... 5 carton Surya Glucose 100g aur 2 bori Aashirvaad 5kg bhej do."*
- **Agent Behavior:**
  - TARS resolves outlet `OUT-006` (Tiwari Provision Store) via RapidFuzz.
  - TARS parses line items: `SKU-001` (Surya Crisp Glucose Biscuit 100g, 5 cartons) and `SKU-041` (Aashirvaad Shuddh Chakki Atta 5kg, 2 bags).
  - TARS evaluates active scheme `SCH-001` (min 5 cartons Glucose → 300 bps / 3.0% trade discount applied automatically).
  - Draft order is created in status `DRAFT`.
  - Action Trace panel displays each tool call (`resolve_outlet`, `add_or_update_line`) with execution latencies (~10-25ms) and status `OK`.
- **Audio Output:**
  > **TARS:** *"Tiwari Provision Store ka order draft ban gaya. 5 carton Surya Glucose 100g par 3% scheme discount lag gaya hai, aur 2 bori Aashirvaad 5kg add ho gaya. Total hai ₹3,682."*

---

### 0:45 – 1:30 | Scene 2: Mid-Sentence Barge-In & Spoken Correction
- **Visual:** TARS begins reading back the draft order for confirmation. Order status indicator shows `CONFIRMING`.
- **Audio (Agent):**
  > **TARS:** *"Tiwari ji ke order confirm karne ke liye main padh raha hoon: 5 carton Glucose..."*
- **Action (Barge-in):** Rep interrupts mid-sentence:
  > **Ravi:** *"Nahi ruko! Tiwari ji keh rahe hain Glucose 5 nahi 10 carton karo, aur Aashirvaad atta cancel karo."*
- **Agent Behavior:**
  - `BargeInEvent` detected on audio stream; TARS stops playback immediately.
  - State machine transitions `CONFIRMING -> DRAFT` (undoing read-back approval gate per `AGENTS.md` §2 rule 3).
  - TARS calls `add_or_update_line` with quantity 10 for Glucose.
  - TARS calls `remove_or_correct_line` to drop the Atta line.
  - Total recalculated to integer paise (`6,080.00` = 608,000 paise).
  - Trace panel logs `BargeInEvent` and tool corrections.
- **Audio Output:**
  > **TARS:** *"Order update ho gaya. Aashirvaad atta hata diya, aur Surya Glucose 10 carton kar diya hai. Naya order total hai ₹6,080. Confirm karein?"*

---

### 1:30 – 2:15 | Scene 3: Credit Limit Breach & Manager Approval Gate
- **Visual:** Rep transitions to the next store on the beat: `OUT-001` Sharma General Store.
- **Action:** Rep speaks order for Sharma ji:
  > **Ravi:** *"Agla order Sharma ji Shastri Nagar. Unka 5 carton Surya Glucose 100g daalo."*
- **Agent Behavior:**
  - TARS resolves outlet `OUT-001` (credit limit: ₹25,000.00; existing outstanding: ₹23,250.00; remaining headroom: ₹1,750.00).
  - Order value is ₹3,040.00. Total exposure becomes ₹26,290.00, breaching credit limit by ₹1,290.00 (`CREDIT_LIMIT_EXCEEDED`).
  - Rules engine marks order `PENDING_APPROVAL` and dispatches `request_manager_approval`.
  - LLM cannot write to ERP or commit directly (`AGENTS.md` §2 rules 1 & 2).
- **Visual (Manager View):**
  - Switch to Manager Queue tab. An approval card appears instantly:
    - **Outlet:** Sharma General Store (`OUT-001`)
    - **Breach:** `CREDIT_LIMIT_EXCEEDED` (Excess: ₹1,290.00)
    - **Order Total:** ₹3,040.00
  - Sales Manager clicks **"Approve"**.
  - State machine transitions order from `PENDING_APPROVAL` to `CONFIRMING`.
- **Audio Output:**
  > **TARS:** *"Sharma General Store ki credit limit cross ho gayi thi, lekin manager ne approval de diya hai. Ab order confirm karne ke liye ready hai."*

---

### 2:15 – 3:00 | Scene 4: Spoken Read-Back, "Haan" Confirmation Gate & ERP Invoice
- **Visual:** Rep screen displays the approved order in `CONFIRMING` status.
- **Audio (Agent):**
  > **TARS:** *"Sharma General Store ke liye 5 carton Surya Glucose 100g, discount ke baad kul raashi hai teen hazaar chaalees rupaye. Kya main order book kar doon?"*
- **Action:** Rep gives explicit spoken confirmation:
  > **Ravi:** *"Haan confirm kar do."*
- **Agent Behavior:**
  - TARS audits `CONFIRMATION_RECEIVED` event linked to previous `READ_BACK_SPOKEN`.
  - State machine transitions `CONFIRMING -> COMMITTED`.
  - `repo.apply_order_commit_effects` executes:
    - Stock decremented by 5 cartons (`SKU-001` stock drops from 140 to 135 units).
    - Outlet outstanding balance incremented by ₹3,040.00.
  - `repo.create_invoice` creates invoice `INV-xxxxxx` in SQLite mock ERP.
- **Visual:**
  - Status badge turns green: **COMMITTED**.
  - Invoice banner appears: **✅ Invoice INV-100001 — ₹3,040.00**.
  - Action Trace panel displays full sequence:
    `resolve_outlet` (14ms) → `add_or_update_line` (18ms) → `request_manager_approval` (12ms) → `record_read_back` (8ms) → `commit_order` (22ms).
- **Audio Output:**
  > **TARS:** *"Order book ho gaya! Invoice number INV-100001 ban gaya hai. Stock update ho gaya aur receipt Sharma ji ko bhej di gayi hai."*

---

## Demo Checklist & Success Criteria

- [x] **Noisy environment handling:** Background ambient chatter present; line parser extracts exact brand and unit.
- [x] **Barge-in correction:** Mid-sentence interruption stops playback, resets state machine to `DRAFT`, recalculates paise.
- [x] **Credit policy enforcement:** ₹1,290 breach halts commit and creates approval ticket for human manager.
- [x] **Spoken confirmation gate:** Spoken `"Haan confirm"` required to commit order.
- [x] **Action Trace visibility:** Every tool execution latency and status displayed in milliseconds on UI.

