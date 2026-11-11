import { useEffect, useState } from 'react'
import type { TraceEntry, TraceStatus } from '../types.ts'

interface Props {
  apiUrl: string
  sessionId: string | null
}

function statusClass(s: TraceStatus): string {
  if (s === 'OK') return 'trace-ok'
  if (s === 'ERROR') return 'trace-error'
  return 'trace-blocked'
}

export function TracePanel({ apiUrl, sessionId }: Props) {
  const [entries, setEntries] = useState<TraceEntry[]>([])

  useEffect(() => {
    const url = sessionId
      ? `${apiUrl}/api/trace?session_id=${sessionId}`
      : `${apiUrl}/api/trace`
    fetch(url)
      .then((r) => r.json())
      .then((data) => setEntries(data as TraceEntry[]))
      .catch(() => { /* ignore fetch errors in tests */ })
  }, [apiUrl, sessionId])

  // ── Computed KPIs ───────────────────────────────────────────
  const count = entries.length

  const medianMs =
    count === 0
      ? null
      : (() => {
          const sorted = [...entries].sort((a, b) => a.latency_ms - b.latency_ms)
          const mid = Math.floor(sorted.length / 2)
          return sorted.length % 2 === 0
            ? Math.round((sorted[mid - 1].latency_ms + sorted[mid].latency_ms) / 2)
            : sorted[mid].latency_ms
        })()

  const maxMs = count === 0 ? null : Math.max(...entries.map((e) => e.latency_ms))

  const p95Ms =
    count === 0
      ? null
      : (() => {
          const sorted = [...entries].sort((a, b) => a.latency_ms - b.latency_ms)
          const idx = Math.min(Math.ceil(sorted.length * 0.95) - 1, sorted.length - 1)
          return sorted[idx].latency_ms
        })()

  const okCount = entries.filter((e) => e.status === 'OK').length
  const blockedCount = entries.filter((e) => e.status === 'BLOCKED').length
  const errorCount = entries.filter((e) => e.status === 'ERROR').length

  return (
    <div className="trace-panel" data-testid="trace-panel">
      {/* ── Header ─────────────────────────────────────────────── */}
      <div className="trace-header">
        <h2 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{ fontSize: '1rem' }}>◉</span>
          Action Trace
        </h2>
        {medianMs !== null && (
          <span className="trace-median" data-testid="trace-median">
            Median: {medianMs}ms
          </span>
        )}
      </div>

      {/* ── KPI bar ────────────────────────────────────────────── */}
      {count > 0 && (
        <div className="trace-kpi-bar">
          <div className="trace-kpi">
            <span className="kpi-label">Total Calls</span>
            <span className="kpi-value">{count}</span>
          </div>
          <div className="trace-kpi">
            <span className="kpi-label">Median</span>
            <span className="kpi-value">{medianMs}ms</span>
          </div>
          <div className="trace-kpi">
            <span className="kpi-label">P95</span>
            <span className="kpi-value">{p95Ms}ms</span>
          </div>
          <div className="trace-kpi">
            <span className="kpi-label">Max</span>
            <span className="kpi-value">{maxMs}ms</span>
          </div>
          <div className="trace-kpi" style={{ marginLeft: 'auto' }}>
            <span className="kpi-label">Status</span>
            <span className="kpi-value" style={{ display: 'flex', gap: '0.5rem', fontSize: '0.75rem' }}>
              <span style={{ color: 'var(--emerald)' }}>✓{okCount}</span>
              {blockedCount > 0 && <span style={{ color: 'var(--amber)' }}>⊘{blockedCount}</span>}
              {errorCount > 0 && <span style={{ color: 'var(--coral)' }}>✕{errorCount}</span>}
            </span>
          </div>
        </div>
      )}

      {/* ── Empty state ────────────────────────────────────────── */}
      {count === 0 && (
        <div className="empty-state">
          <p style={{ fontSize: '1.5rem', marginBottom: '0.5rem' }}>◌</p>
          <p>No trace entries yet.</p>
          <p style={{ fontSize: '0.75rem', color: 'var(--text-dim)', marginTop: '0.25rem' }}>
            Tool calls appear here in real-time as the agent processes orders.
          </p>
        </div>
      )}

      {/* ── Trace list ─────────────────────────────────────────── */}
      <ul className="trace-list">
        {entries.map((e) => (
          <li key={e.event_id} className="trace-entry" data-testid="trace-entry">
            <code className="trace-tool" data-testid="trace-tool">{e.tool_name}</code>
            <span className={`trace-status ${statusClass(e.status)}`} data-testid="trace-status">
              {e.status}
            </span>
            {e.order_id && (
              <span style={{
                fontSize: '0.6rem',
                fontFamily: 'var(--font-mono)',
                color: 'var(--text-dim)',
              }}>
                {e.order_id}
              </span>
            )}
            <span className="trace-latency" data-testid="trace-latency">{e.latency_ms}ms</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
