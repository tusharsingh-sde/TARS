# TARS: Resilience, Multilingual & Exception Test Utterances

This document records the test utterances, expected behaviors, and verified outcomes for Phase 6 (`P6-01` through `P6-04`).

---

## 1. P6-01: Noisy Audio Test Clips & Low-Confidence Outcomes

Deterministic 16kHz 16-bit mono PCM test audio fixtures are generated and validated in `demo/recordings/`:

| Audio Clip File | Duration | Format | Description / Noise Profile | Pipeline Outcome |
|---|---|---|---|---|
| `noisy_shop_order_16k.wav` | 1.0s (16,000 samples) | 16-bit PCM mono 16kHz | 50Hz AC mains hum + random shop chatter hiss + speech envelope formants (350Hz, 900Hz, 2400Hz) | Processed into 100ms sample-aligned PCM frames (3,200 bytes); `validate_pcm16_frame` and `chunk_pcm16` succeed. |
| `clipped_low_snr_16k.wav` | 1.0s (16,000 samples) | 16-bit PCM mono 16kHz | Low SNR vocal tone (2,500 amplitude) buried under ±9,000 noise floor with ±45,000 peak clamping bursts | Raw audio validates cleanly; garbled speech transcript triggers low-confidence disambiguation. |
| `barge_in_correction_16k.wav` | 1.0s (16,000 samples) | 16-bit PCM mono 16kHz | 400ms agent read-back tones (440Hz/880Hz) → 100ms silence → sharp 500ms rep interruption burst | Validates barge-in cutoff detection; triggers prompt truncation without audio buffer misalignment. |

### Low-Confidence & Inaudible Transcripts (Zero-Guess Rule)

| Input Utterance / Transcript | Tool Invoked | Match Status | Order Lines Affected | Agent Response / Behavior |
|---|---|---|---|---|
| `"... [inaudible] ... 5 peti ..."` | `add_or_update_line` | `NOT_FOUND` | 0 (untouched) | Low-confidence; asks rep to repeat product name and pack size. |
| `"[static noise] [cough] khrrrr"` | `add_or_update_line` | `NOT_FOUND` | 0 (untouched) | Inaudible noise rejected; asks rep to repeat. |
| `"5 cartons"` | `add_or_update_line` | `NOT_FOUND` | 0 (untouched) | Missing product name; asks rep to specify item. |

---

## 2. P6-02 & P6-03: Multilingual & Code-Switched Utterances

TARS parses numbers, packs, units, and regional trade terminology across Hinglish, Tanglish (Tamil + English), and Marathi/Minglish. Conversational fillers are stripped cleanly without polluting catalog matching.

### Tanglish (Tamil + English) Test Set

| Spoken Utterance | Number Token | Unit Mapped | Pack Size | Resolved Product | Catalog SKU | Verified Total |
|---|---|---|---|---|---|---|
| `"rendu peti surya glucose 100g kudunga"` | `rendu` (2) | `peti` → `carton` | `100g` | `surya glucose` | `SKU-001` (Surya Crisp Glucose 100g) | 2 × ₹640.00 = ₹1,280.00 |
| `"moonu bag chamak 1kg podunga"` | `moonu` (3) | `bag` → `bag` | `1kg` | `chamak` | `SKU-017` (Chamak Plus Detergent 1kg) | 3 × ₹115.00 = ₹345.00 |
| `"naalu piece lemon gel 500ml venum annachi"` | `naalu` (4) | `piece` → `piece` | `500ml` | `lemon gel` | `SKU-021` (Chamak Plus Dishwash Gel 500ml) | 4 × ₹98.00 = ₹392.00 |
| `"onnu box swadista noodles 70g kudu"` | `onnu` (1) | `box` → `carton` | `70g` | `swadista noodles` | `SKU-009` (Swadista Noodles 70g) | 1 × ₹1,180.00 = ₹1,180.00 |
| `"anju packet tea 250g podu"` | `anju` (5) | `packet` → `bag` | `250g` | `tea` | Parsed quantity=5, unit=bag, pack=250g | Accurate parse |

### Marathi / Minglish Test Set

| Spoken Utterance | Number Token | Unit Mapped | Pack Size | Resolved Product | Catalog SKU | Verified Total |
|---|---|---|---|---|---|---|
| `"don bori aashirvaad 5kg dya"` | `don` (2) | `bori` → `bag` | `5kg` | `aashirvaad` | Resolved to Aashirvaad 5kg | Accurate parse |
| `"paach box swadista noodles 70g pathva"` | `paach` (5) | `box` → `carton` | `70g` | `swadista noodles` | `SKU-009` (Swadista Noodles 70g) | 5 × ₹1,180.00 = ₹5,900.00 |
| `"daha packet poha 500g taka"` | `daha` (10) | `packet` → `bag` | `500g` | `poha` | `SKU-015` (Swadista Kanda Poha 500g) | 10 × ₹52.00 = ₹520.00 |
| `"teen pishvi detergent 2kg pahije"` | `teen` (3) | `pishvi` → `bag` | `2kg` | `detergent` | Resolved with unit=bag, pack=2kg | Accurate parse |
| `"ek dabba pickle 200g dya"` | `ek` (1) | `dabba` → `piece` | `200g` | `pickle` | Resolved with unit=piece, pack=200g | Accurate parse |

---

## 3. P6-04: Exception Scenarios Matrix

| Scenario ID | Test Case | Spoken Input | System Response / Decision | Verification Assertion |
|---|---|---|---|---|
| **EX-01** | Ambiguous Outlet | `"sharma"` | `status: AMBIGUOUS`<br>`candidates: [OUT-001, OUT-002]`<br>`order_id: None` | Draft order is NOT opened; rep asked to clarify locality. |
| **EX-02** | Unknown Outlet | `"xyzabcdef qrstuvwxyz 999"` | `status: NOT_FOUND`<br>`order_id: None` | Refuses to open draft; no silent fallback to random outlet. |
| **EX-03** | Order Line before Outlet | `"5 cartons bhoomi salt 1kg"` (no active outlet) | `status: BLOCKED`<br>`reason: NO_ACTIVE_ORDER` | Line addition blocked until outlet is confirmed. |
| **EX-04** | Ambiguous SKU (Missing Pack) | `"5 cartons surya glucose"` | `status: AMBIGUOUS`<br>`needs_pack: True`<br>`candidates: [100g, 50g]` | Draft order lines count remains 0; rep asked for pack size. |
| **EX-05** | Ambiguous SKU Clarified | `"5 cartons surya glucose 100g"` | `status: OK`<br>`sku_id: SKU-001` | Line successfully added only after pack size is provided. |
| **EX-06** | Out of Stock (SKU-019) | `"2 bags stain buster 2kg"` | `status: BLOCKED`<br>`reason: LOW_STOCK`<br>`shortfall: 2` | Line blocked; draft remains empty; rep informed of 0 stock. |
| **EX-07** | Out of Stock (SKU-038) | `"5 cartons spring mineral water 1L"` | `status: BLOCKED`<br>`reason: LOW_STOCK`<br>`shortfall: 5` | Line blocked; draft remains empty. |
| **EX-08** | Out of Stock (SKU-057) | `"3 cartons classic salted chips 52g"` | `status: BLOCKED`<br>`reason: LOW_STOCK`<br>`shortfall: 3` | Line blocked; draft remains empty. |
| **EX-09** | Out of Stock (SKU-076) | `"4 piece dark soy sauce 200g"` | `status: BLOCKED`<br>`reason: LOW_STOCK`<br>`shortfall: 4` | Line blocked; draft remains empty. |
| **EX-10** | Multilingual Commit | Tanglish item + Marathi item → Spoken read-back → `"confirm"` | `status: OK`<br>`order_status: COMMITTED`<br>`invoice: INV-...` | Both items preserved; correct total in paise; invoice generated. |

---

## 4. Test Summary & Verification Gate

- **Total Resilience & Multilingual Test Count:** 37 tests in `backend/tests/test_resilience_and_languages.py`
- **Pass Rate:** 100% (37 / 37 passed)
- **Zero Guesses Enforced:** 100% of ambiguous/low-confidence/garbled inputs reject silent assignment.

