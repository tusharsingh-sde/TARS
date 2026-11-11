import { useEffect, useState } from 'react'
import type { ApprovalWithSummary } from '../types.ts'
import { paisaToRupees } from '../types.ts'

interface Props {
  apiUrl: string
}

export function ManagerQueue({ apiUrl }: Props) {
  const [items, setItems] = useState<ApprovalWithSummary[]>([])
  const [deciding, setDeciding] = useState<string | null>(null)

  async function fetchApprovals() {
    const resp = await fetch(`${apiUrl}/api/approvals?status=PENDING`)
    if (resp.ok) {
      setItems((await resp.json()) as ApprovalWithSummary[])
    }
  }

  useEffect(() => {
    void fetchApprovals()
    const timer = setInterval(() => { void fetchApprovals() }, 5000)
    return () => clearInterval(timer)
  }, [apiUrl]) // eslint-disable-line react-hooks/exhaustive-deps

  async function decide(requestId: string, approved: boolean) {
    setDeciding(requestId)
    await fetch(`${apiUrl}/api/approvals/${requestId}/decide`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approved, decided_by: 'manager-web' }),
    })
    setDeciding(null)
    void fetchApprovals()
  }

  const pendingCount = items.length

  return (
    <div className="manager-queue" data-testid="manager-queue">
      {/* ── Header ─────────────────────────────────────────────── */}
      <div className="trace-header" style={{ marginBottom: '1rem' }}>
        <h2 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{ fontSize: '1.1rem' }}>⚠</span>
          Approval Queue
          {pendingCount > 0 && (
            <span
              style={{
                fontSize: '0.65rem',
                fontWeight: 700,
                background: 'var(--coral-bg)',
                color: 'var(--coral)',
                border: '1px solid rgba(239, 68, 68, 0.25)',
                borderRadius: '999px',
                padding: '0.15rem 0.5rem',
                fontVariantNumeric: 'tabular-nums',
                fontFamily: 'var(--font-mono)',
              }}
            >
              {pendingCount} PENDING
            </span>
          )}
        </h2>
      </div>

      {/* ── Empty state ────────────────────────────────────────── */}
      {items.length === 0 && (
        <div className="empty-state">
          <p style={{ fontSize: '1.5rem', marginBottom: '0.5rem' }}>✓</p>
          <p>No pending approvals.</p>
          <p style={{ fontSize: '0.75rem', color: 'var(--text-dim)', marginTop: '0.25rem' }}>
            Approval requests appear here when orders breach credit or discount limits.
          </p>
        </div>
      )}

      {/* ── Approval cards ─────────────────────────────────────── */}
      {items.map((item) => {
        const isDeciding = deciding === item.approval.request_id

        return (
          <div
            key={item.approval.request_id}
            className="approval-card"
            data-testid="approval-card"
            style={isDeciding ? { opacity: 0.6, pointerEvents: 'none' } : undefined}
          >
            {/* Card header: outlet + reason badge + IDs */}
            <div className="approval-header">
              <strong data-testid="approval-outlet-name">{item.outlet_name}</strong>
              <span
                className="reason-badge"
                data-testid="reason-badge"
                data-reason={item.approval.reason_code}
              >
                {item.approval.reason_code}
              </span>
            </div>

            {/* Meta row: IDs + timestamp */}
            <div style={{
              display: 'flex',
              gap: '0.75rem',
              flexWrap: 'wrap',
              fontSize: '0.7rem',
              color: 'var(--text-dim)',
              fontFamily: 'var(--font-mono)',
              margin: '0.35rem 0',
            }}>
              <span>REQ {item.approval.request_id}</span>
              <span>ORD {item.approval.order_id}</span>
              {item.approval.created_at && (
                <span>{new Date(item.approval.created_at).toLocaleTimeString()}</span>
              )}
            </div>

            {/* Order total */}
            <p>Order total: <span data-testid="approval-total">{paisaToRupees(item.order_total_paise)}</span></p>

            {/* Action buttons */}
            <div className="approval-actions">
              <button
                className="btn-approve"
                data-testid="btn-approve"
                disabled={isDeciding}
                onClick={() => void decide(item.approval.request_id, true)}
              >
                ✓ Approve
              </button>
              <button
                className="btn-reject"
                data-testid="btn-reject"
                disabled={isDeciding}
                onClick={() => void decide(item.approval.request_id, false)}
              >
                ✕ Reject
              </button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
