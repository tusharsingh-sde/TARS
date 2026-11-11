import { useEffect, useState } from 'react'

type HealthState = 'checking' | 'ok' | 'unreachable'

interface HealthPayload {
  status?: unknown
}

export function App() {
  const [health, setHealth] = useState<HealthState>('checking')

  useEffect(() => {
    const rawBaseUrl = import.meta.env.VITE_API_URL
    const baseUrl = typeof rawBaseUrl === 'string' ? rawBaseUrl.trim().replace(/\/+$/, '') : ''

    if (!baseUrl) {
      setHealth('unreachable')
      return
    }

    const controller = new AbortController()

    async function checkHealth() {
      try {
        const response = await fetch(`${baseUrl}/health`, {
          signal: controller.signal,
        })
        if (!response.ok) {
          setHealth('unreachable')
          return
        }
        const payload = (await response.json()) as HealthPayload
        setHealth(payload.status === 'ok' ? 'ok' : 'unreachable')
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          return
        }
        setHealth('unreachable')
      }
    }

    void checkHealth()

    return () => {
      controller.abort()
    }
  }, [])

  const statusText =
    health === 'checking'
      ? 'Backend: checking...'
      : health === 'ok'
        ? 'Backend: ok'
        : 'Backend: unreachable'

  return (
    <main className="app-shell">
      <section className="status-card">
        <h1>TARS: rep console (coming soon)</h1>
        <p className="health-status">{statusText}</p>
      </section>
    </main>
  )
}

export default App
