import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.tsx'

afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('App', () => {
  it('renders the heading and checks backend health', async () => {
    vi.stubEnv('VITE_API_URL', 'http://backend.test')
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok', version: '0.1.0' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    const heading = screen.getByRole('heading', {
      name: 'TARS: rep console (coming soon)',
    })
    expect(heading).toBeTruthy()

    await waitFor(() => {
      expect(screen.getByText('Backend: ok')).toBeTruthy()
    })
    expect(fetchMock).toHaveBeenCalledWith(
      'http://backend.test/health',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })
})

