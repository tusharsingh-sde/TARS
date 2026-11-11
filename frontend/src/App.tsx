import { useEffect, useState } from 'react'
import { RepScreen } from './rep/RepScreen.tsx'
import { ManagerQueue } from './manager/ManagerQueue.tsx'
import { TracePanel } from './trace/TracePanel.tsx'

type Tab = 'rep' | 'manager' | 'trace'
type HealthState = 'checking' | 'ok' | 'unreachable'

interface HealthPayload {
  status?: unknown
}

function getApiUrl(): string {
  const rawBaseUrl = import.meta.env.VITE_API_URL
  return typeof rawBaseUrl === 'string' ? rawBaseUrl.trim().replace(/\/+$/, '') : ''
}

export function App() {
  const [tab, setTab] = useState<Tab>('rep')
  const [health, setHealth] = useState<HealthState>('checking')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [resetting, setResetting] = useState(false)
  const apiUrl = getApiUrl()

  useEffect(() => {
    if (!apiUrl) {
      setHealth('unreachable')
      return
    }
    const controller = new AbortController()
    async function checkHealth() {
      try {
        const response = await fetch(`${apiUrl}/health`, { signal: controller.signal })
        if (!response.ok) { setHealth('unreachable'); return }
        const payload = (await response.json()) as HealthPayload
        setHealth(payload.status === 'ok' ? 'ok' : 'unreachable')
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setHealth('unreachable')
      }
    }
    void checkHealth()
    return () => controller.abort()
  }, [apiUrl])

  async function handleResetDemo() {
    if (!apiUrl || resetting) return
    setResetting(true)
    try {
      await fetch(`${apiUrl}/api/admin/reset-demo`, { method: 'POST' })
      window.location.reload()
    } catch {
      setResetting(false)
    }
  }

  const statusText =
    health === 'checking' ? 'Backend: checking…'
    : health === 'ok' ? 'Backend: ok'
    : 'Backend: unreachable'

  const healthPillClass = `pill pill-health ${health}`

  return (
    <main className="app-shell">
      <header className="app-header">
        <h1>TARS</h1>
        <div className="header-pills">
          <span className="pill pill-model">⚡ gemini-3.8-live</span>
          <span className="pill pill-route">📍 R-NORTH</span>
          <span className={healthPillClass}>
            {health === 'ok' ? '●' : health === 'checking' ? '◌' : '✕'}&nbsp;
            {health === 'ok' ? 'LIVE' : health === 'checking' ? 'CONNECTING' : 'OFFLINE'}
          </span>
        </div>
        <div className="header-actions">
          <button
            className="btn-reset-demo"
            onClick={() => void handleResetDemo()}
            disabled={resetting}
          >
            {resetting ? 'Resetting…' : '↻ Reset Demo'}
          </button>
        </div>
        {/* Preserved for test compat: invisible but in DOM */}
        <p className="health-status" data-testid="health-status">{statusText}</p>
      </header>

      <nav className="tab-bar" data-testid="tab-bar" role="tablist">
        <button
          role="tab"
          aria-selected={tab === 'rep'}
          className={tab === 'rep' ? 'tab active' : 'tab'}
          onClick={() => setTab('rep')}
        >
          Rep View
        </button>
        <button
          role="tab"
          aria-selected={tab === 'manager'}
          className={tab === 'manager' ? 'tab active' : 'tab'}
          onClick={() => setTab('manager')}
        >
          Manager Queue
        </button>
        <button
          role="tab"
          aria-selected={tab === 'trace'}
          className={tab === 'trace' ? 'tab active' : 'tab'}
          onClick={() => setTab('trace')}
        >
          Trace
        </button>
      </nav>

      <section className="tab-content">
        {tab === 'rep' && (
          <RepScreen apiUrl={apiUrl} onSessionId={setSessionId} sessionId={sessionId} />
        )}
        {tab === 'manager' && <ManagerQueue apiUrl={apiUrl} />}
        {tab === 'trace' && <TracePanel apiUrl={apiUrl} sessionId={sessionId} />}
      </section>
    </main>
  )
}

export default App
