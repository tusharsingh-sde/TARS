import { useEffect, useRef, useState } from 'react'
import type { OrderDTO, OutletDTO, InvoiceDTO, TraceEntry } from '../types.ts'
import { paisaToRupees } from '../types.ts'

interface Props {
  apiUrl: string
  onSessionId: (id: string) => void
  sessionId: string | null
}

interface Bubble {
  id: number
  role: 'rep' | 'tars'
  text: string
}

type VoiceState = 'idle' | 'listening' | 'speaking' | 'interrupted'

const DEMO_CHIPS = [
  { label: '1. Match Sharma Kirana', utterance: 'Sharma Kirana' },
  { label: '2. Add 5 Peti Surya Glucose', utterance: '5 peti surya glucose 100g' },
  { label: '3. Barge-in: Change to 2 Peti', utterance: '2 peti surya glucose 100g' },
  { label: '4. Haan Confirm Kar Do', utterance: 'haan confirm kar do' },
]

const GATE_STEPS = [
  { key: 'draft', label: 'Draft' },
  { key: 'rules', label: 'Rules Checked' },
  { key: 'readback', label: 'Voice Read-Back' },
  { key: 'committed', label: 'Confirmed & Invoiced' },
]

function getGateIndex(status: string | undefined, readBackSpoken?: boolean): number {
  if (!status) return 0
  if (status === 'COMMITTED') return 4
  if (status === 'CONFIRMING') return readBackSpoken ? 3 : 2
  if (status === 'PENDING_APPROVAL') return 1
  return 0
}

let _bubbleId = 0

export function RepScreen({ apiUrl, onSessionId, sessionId }: Props) {
  const [order, setOrder] = useState<OrderDTO | null>(null)
  const [outlet, setOutlet] = useState<OutletDTO | null>(null)
  const [invoice, setInvoice] = useState<InvoiceDTO | null>(null)
  const [bubbles, setBubbles] = useState<Bubble[]>([])
  const [input, setInput] = useState('')
  const [recording, setRecording] = useState(false)
  const [ttfa, setTtfa] = useState<number | null>(null)
  const [voiceState, setVoiceState] = useState<VoiceState>('idle')
  const wsRef = useRef<WebSocket | null>(null)
  const sidRef = useRef<string | null>(sessionId)
  const feedRef = useRef<HTMLDivElement | null>(null)

  // Auto-scroll transcript to bottom
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight
    }
  }, [bubbles])

  // Create session on mount
  useEffect(() => {
    async function createSession() {
      const resp = await fetch(`${apiUrl}/api/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rep_id: 'REP-WEB-01' }),
      })
      if (!resp.ok) return
      const data = (await resp.json()) as { session_id: string; rep_id: string }
      sidRef.current = data.session_id
      onSessionId(data.session_id)

      // Open WebSocket — may throw in test environments with partial mocks
      const wsUrl = apiUrl.replace(/^http/, 'ws')
      let ws: WebSocket | null = null
      try {
        ws = new WebSocket(`${wsUrl}/ws/voice/${data.session_id}`)
        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(ev.data as string) as Record<string, unknown>
            if (msg.type === 'transcript' && typeof msg.text === 'string') {
              const role = msg.role === 'user' ? 'rep' : 'tars'
              setBubbles((prev) => [...prev, { id: ++_bubbleId, role, text: msg.text as string }])
              setVoiceState(role === 'rep' ? 'listening' : 'speaking')
            }
            if (msg.type === 'ttfa' && typeof msg.ms === 'number') {
              setTtfa(msg.ms as number)
            }
            if (msg.type === 'barge_in') {
              setVoiceState('interrupted')
              setTimeout(() => setVoiceState('idle'), 1500)
            }
          } catch {
            /* ignore non-JSON frames */
          }
        }
        wsRef.current = ws
      } catch {
        /* WebSocket unavailable (test environment) — continue without it */
      }
    }
    void createSession()
    return () => {
      wsRef.current?.close()
    }
  }, [apiUrl, onSessionId])

  async function dispatchTool(name: string, args: Record<string, unknown>) {
    const sid = sidRef.current
    if (!sid) return
    const start = Date.now()
    const resp = await fetch(`${apiUrl}/api/sessions/${sid}/tool`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, args }),
    })
    if (!resp.ok) return
    const body = (await resp.json()) as { result: Record<string, unknown>; trace_entries: TraceEntry[] }
    const elapsed = Date.now() - start
    setTtfa(elapsed)

    // Refresh order if we can find an order_id
    const result = body.result
    const orderId =
      typeof result['order_id'] === 'string'
        ? result['order_id']
        : order?.order_id

    if (orderId) {
      const oResp = await fetch(`${apiUrl}/api/orders/${orderId}`)
      if (oResp.ok) {
        const detail = (await oResp.json()) as {
          order: OrderDTO
          outlet: OutletDTO
          invoice: InvoiceDTO | null
        }
        setOrder(detail.order)
        setOutlet(detail.outlet)
        setInvoice(detail.invoice)
      }
    }
  }

  function handleSend(text?: string) {
    const msg = (text ?? input).trim()
    if (!msg) return
    setBubbles((prev) => [...prev, { id: ++_bubbleId, role: 'rep', text: msg }])
    // Simulate a text-mode add_or_update_line call
    void dispatchTool('add_or_update_line', { utterance_or_product: msg })
    if (!text) setInput('')
  }

  const creditUsedPct =
    outlet && outlet.credit_limit_paise > 0
      ? Math.min(100, Math.round(((outlet.outstanding_paise + (order?.total_paise ?? 0)) / outlet.credit_limit_paise) * 100))
      : 0

  const creditBarClass =
    creditUsedPct > 100 ? 'credit-bar-fill danger'
    : creditUsedPct >= 75 ? 'credit-bar-fill warning'
    : 'credit-bar-fill'

  const gateIdx = getGateIndex(order?.status)

  const voiceLabel =
    voiceState === 'listening' ? 'Listening 16kHz PCM'
    : voiceState === 'speaking' ? 'TARS Speaking 24kHz'
    : voiceState === 'interrupted' ? 'Barge-In Interrupted!'
    : 'Idle — Tap Mic to Begin'

  const eqClass =
    voiceState === 'listening' || voiceState === 'speaking' ? 'eq-bars active'
    : voiceState === 'interrupted' ? 'eq-bars active speaking'
    : 'eq-bars idle'

  return (
    <div className="rep-screen" data-testid="rep-screen">
      {/* Voice state indicator */}
      <div className={`voice-state-ring ${voiceState}`}>
        <div className={eqClass}>
          <div className="eq-bar" style={{ height: 6 }} />
          <div className="eq-bar" style={{ height: 10 }} />
          <div className="eq-bar" style={{ height: 8 }} />
          <div className="eq-bar" style={{ height: 12 }} />
          <div className="eq-bar" style={{ height: 7 }} />
        </div>
        <span>{voiceLabel}</span>
        {ttfa !== null && (
          <span className="ttfa-pill" data-testid="ttfa-pill">
            TTFA: {ttfa}ms
          </span>
        )}
      </div>

      {/* Transcript feed */}
      <div className="transcript-feed" data-testid="transcript-feed" ref={feedRef}>
        {bubbles.length === 0 && (
          <p className="empty-state" style={{ fontSize: '0.8rem' }}>
            Speak an order or tap a demo chip below…
          </p>
        )}
        {bubbles.map((b) => (
          <div key={b.id} className={`bubble bubble-${b.role}`}>
            <span style={{ fontSize: '0.6rem', fontWeight: 700, opacity: 0.6, marginRight: '0.4rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              {b.role === 'rep' ? 'REP' : 'TARS'}
            </span>
            {b.text}
          </div>
        ))}
      </div>

      {/* Demo quick-prompt chips */}
      <div className="demo-chips">
        {DEMO_CHIPS.map((chip) => (
          <button
            key={chip.label}
            className="demo-chip"
            onClick={() => handleSend(chip.utterance)}
          >
            {chip.label}
          </button>
        ))}
      </div>

      {/* Text input bar */}
      <div className="input-bar" data-testid="input-bar">
        <button
          className={recording ? 'mic-button recording' : 'mic-button'}
          data-testid="mic-button"
          aria-label={recording ? 'Stop recording' : 'Start recording'}
          onClick={() => {
            setRecording((r) => !r)
            setVoiceState((v) => v === 'idle' ? 'listening' : 'idle')
          }}
        >
          {recording ? '⏹' : '🎙'}
        </button>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleSend() }}
          placeholder="Type order (e.g. 5 cartons Tata Salt 1kg)…"
          aria-label="Order input"
        />
        <button className="btn-send" onClick={() => handleSend()} aria-label="Send">Send</button>
      </div>

      {/* Draft order view */}
      {outlet && (
        <div className="draft-order" data-testid="draft-order">
          <div className="outlet-header">
            <div>
              <strong data-testid="outlet-name">{outlet.name}</strong>
              <span className="locality"> · {outlet.locality}</span>
            </div>
            {order && (
              <span className={`status-badge status-${order.status.toLowerCase()}`} data-testid="status-badge">
                {order.status.replace('_', ' ')}
              </span>
            )}
          </div>

          <div className="credit-bar-wrap">
            <label className="credit-figures">
              Credit: {paisaToRupees(outlet.outstanding_paise + (order?.total_paise ?? 0))} / {paisaToRupees(outlet.credit_limit_paise)}
              <span style={{ marginLeft: '0.5rem', fontSize: '0.7rem', color: creditUsedPct > 100 ? 'var(--coral)' : 'var(--text-muted)' }}>
                ({creditUsedPct}%)
              </span>
            </label>
            <div className="credit-bar">
              <div
                className={creditBarClass}
                style={{ width: `${Math.min(creditUsedPct, 100)}%` }}
                data-testid="credit-bar-fill"
              />
            </div>
          </div>

          {/* Read-Back Gate Stepper */}
          {order && (
            <div className="gate-stepper">
              {GATE_STEPS.map((step, idx) => (
                <div key={step.key} style={{ display: 'flex', alignItems: 'center' }}>
                  <div className={`gate-step ${idx < gateIdx ? 'completed' : idx === gateIdx ? 'current' : ''}`}>
                    <span className="gate-step-dot" />
                    <span>{step.label}</span>
                  </div>
                  {idx < GATE_STEPS.length - 1 && (
                    <div className={`gate-connector${idx < gateIdx ? ' completed' : ''}`}
                      style={{ background: idx < gateIdx ? 'var(--emerald)' : undefined }}
                    />
                  )}
                </div>
              ))}
            </div>
          )}

          {order && (
            <>
              <table className="order-lines" data-testid="order-lines">
                <thead>
                  <tr>
                    <th>SKU</th>
                    <th>Qty</th>
                    <th>Price</th>
                    <th>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {order.lines.map((line) => (
                    <tr key={line.id}>
                      <td style={{ fontFamily: 'var(--font-ui)', fontWeight: 600, color: 'var(--text-primary)' }}>{line.sku_id}</td>
                      <td>{line.quantity}</td>
                      <td>{paisaToRupees(line.unit_price_paise)}</td>
                      <td>
                        {paisaToRupees(line.line_total_paise)}
                        {line.discount_bps > 0 && (
                          <span className="discount-badge">
                            -{(line.discount_bps / 100).toFixed(2)}%
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={3}><strong style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-ui)' }}>Order Total</strong></td>
                    <td data-testid="order-total"><strong style={{ color: 'var(--emerald)', fontSize: '1rem' }}>{paisaToRupees(order.total_paise)}</strong></td>
                  </tr>
                </tfoot>
              </table>
            </>
          )}

          {invoice && (
            <div className="invoice-banner" data-testid="invoice-banner">
              <span style={{ fontSize: '1.2rem' }}>✅</span>
              <span>Invoice</span>
              <span className="inv-id">{invoice.invoice_id}</span>
              <span style={{ marginLeft: 'auto' }}>{paisaToRupees(invoice.total_paise)}</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
