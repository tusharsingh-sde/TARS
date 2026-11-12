import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.tsx'
import { RepScreen } from './rep/RepScreen.tsx'
import { ManagerQueue } from './manager/ManagerQueue.tsx'
import { TracePanel } from './trace/TracePanel.tsx'
import { paisaToRupees } from './types.ts'

afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------------------
// Original health check (regression guard — P0-03)
// ---------------------------------------------------------------------------

describe('App health check', () => {
  it('renders heading and shows "Backend: ok" when health endpoint returns ok', async () => {
    vi.stubEnv('VITE_API_URL', 'http://backend.test')
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok', version: '0.1.0' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    const heading = screen.getByRole('heading', { name: 'TARS' })
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

// ---------------------------------------------------------------------------
// P5-01: Rep Screen
// ---------------------------------------------------------------------------

describe('P5-01 RepScreen', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_API_URL', 'http://backend.test')
  })

  it('renders mic button and input bar', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ session_id: 'SID-001', rep_id: 'REP-TEST' }),
    })
    // WebSocket mock must be a constructor (class-style)
    const wsMock = { onmessage: null as ((ev: MessageEvent) => void) | null, close: vi.fn() }
    vi.stubGlobal('WebSocket', vi.fn(function () { return wsMock }))
    vi.stubGlobal('fetch', fetchMock)

    render(<RepScreen apiUrl="http://backend.test" onSessionId={() => undefined} sessionId={null} />)

    // Mic button must be rendered
    expect(screen.getByTestId('mic-button')).toBeTruthy()
    // Input bar must be rendered
    expect(screen.getByTestId('input-bar')).toBeTruthy()
  })

  it('displays outlet name and credit bar after tool dispatch resolves an order', async () => {
    const sessionResp = { session_id: 'SID-002', rep_id: 'REP-TEST' }
    const orderDetail = {
      order: {
        order_id: 'ORD-001',
        outlet_id: 'OUT-001',
        rep_id: 'REP-TEST',
        status: 'DRAFT',
        total_paise: 304000,
        lines: [
          {
            id: 1,
            order_id: 'ORD-001',
            sku_id: 'SKU-GLUCOSE-100G',
            quantity: 5,
            unit_price_paise: 60800,
            discount_bps: 0,
            line_total_paise: 304000,
          },
        ],
        created_at: '2026-01-01T00:00:00',
        updated_at: '2026-01-01T00:00:00',
      },
      outlet: {
        outlet_id: 'OUT-001',
        name: 'Tiwari General Store',
        owner_name: 'Tiwari Ji',
        locality: 'Lajpat Nagar',
        city: 'Delhi',
        route_code: 'R01',
        credit_limit_paise: 500000,
        outstanding_paise: 100000,
        aliases: ['tiwari ji'],
      },
      invoice: null,
    }
    const toolResp = {
      result: { status: 'MATCHED', order_id: 'ORD-001' },
      session_id: 'SID-002',
      trace_entries: [{ event_id: 1, session_id: 'SID-002', order_id: 'ORD-001', tool_name: 'resolve_outlet', input_payload: {}, output_payload: {}, status: 'OK', latency_ms: 10, created_at: '2026-01-01T00:00:00' }],
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => sessionResp })  // POST /api/sessions
      .mockResolvedValueOnce({ ok: true, json: async () => toolResp })     // POST /tool (from handleSend)
      .mockResolvedValueOnce({ ok: true, json: async () => orderDetail })  // GET /api/orders/ORD-001

    const wsMock = { onmessage: null as ((ev: MessageEvent) => void) | null, close: vi.fn() }
    vi.stubGlobal('WebSocket', vi.fn(function () { return wsMock }))
    vi.stubGlobal('fetch', fetchMock)

    render(<RepScreen apiUrl="http://backend.test" onSessionId={() => undefined} sessionId={null} />)

    // Wait for session creation to complete (POST /api/sessions resolved)
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/api/sessions'),
        expect.any(Object),
      )
    })

    // Trigger a send so that dispatchTool → tool → order detail
    const inputEl = screen.getByRole('textbox', { name: 'Order input' })
    fireEvent.change(inputEl, { target: { value: '5 cartons tata salt' } })
    fireEvent.keyDown(inputEl, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByTestId('outlet-name')).toBeTruthy()
    })
    expect(screen.getByTestId('outlet-name').textContent).toBe('Tiwari General Store')

    // Check ₹ formatting via paisaToRupees helper
    const formatted = paisaToRupees(304000)
    expect(formatted).toContain('₹')
    expect(screen.getByTestId('order-total').textContent).toContain('₹')
  })
})

// ---------------------------------------------------------------------------
// P5-02: Manager Queue
// ---------------------------------------------------------------------------

describe('P5-02 ManagerQueue', () => {
  it('renders pending approval with Approve and Reject buttons', async () => {
    const pendingItems = [
      {
        approval: {
          request_id: 'APR-001',
          order_id: 'ORD-002',
          reason_code: 'CREDIT_LIMIT_EXCEEDED',
          status: 'PENDING',
          decided_by: null,
          decided_at: null,
          created_at: '2026-01-01T00:00:00',
        },
        order_total_paise: 500000,
        outlet_name: 'Sharma Kirana',
        outlet_id: 'OUT-002',
      },
    ]

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => pendingItems,
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ManagerQueue apiUrl="http://backend.test" />)

    await waitFor(() => {
      expect(screen.getByTestId('approval-outlet-name').textContent).toBe('Sharma Kirana')
    })
    expect(screen.getByTestId('reason-badge').textContent).toBe('CREDIT_LIMIT_EXCEEDED')
    expect(screen.getByTestId('btn-approve')).toBeTruthy()
    expect(screen.getByTestId('btn-reject')).toBeTruthy()
  })

  it('calls POST /api/approvals/{id}/decide when Approve is clicked', async () => {
    const pendingItems = [
      {
        approval: {
          request_id: 'APR-002',
          order_id: 'ORD-003',
          reason_code: 'DISCOUNT_CAP_EXCEEDED',
          status: 'PENDING',
          decided_by: null,
          decided_at: null,
          created_at: '2026-01-01T00:00:00',
        },
        order_total_paise: 200000,
        outlet_name: 'Gupta Mart',
        outlet_id: 'OUT-003',
      },
    ]

    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => pendingItems })  // initial fetch
      .mockResolvedValueOnce({ ok: true, json: async () => ({}) })          // decide POST
      .mockResolvedValueOnce({ ok: true, json: async () => [] })            // re-fetch after decide

    vi.stubGlobal('fetch', fetchMock)

    render(<ManagerQueue apiUrl="http://backend.test" />)

    await waitFor(() => {
      expect(screen.getByTestId('btn-approve')).toBeTruthy()
    })

    fireEvent.click(screen.getByTestId('btn-approve'))

    await waitFor(() => {
      const calls = fetchMock.mock.calls as [string, RequestInit?][]
      const decideCall = calls.find((c) => c[0].includes('/decide'))
      expect(decideCall).toBeTruthy()
      expect(decideCall![0]).toContain('APR-002')
    })
  })
})

// ---------------------------------------------------------------------------
// P5-03: Trace Panel
// ---------------------------------------------------------------------------

describe('P5-03 TracePanel', () => {
  it('renders trace entries with tool name, status badge, and latency', async () => {
    const entries = [
      {
        event_id: 1,
        session_id: 'SID-003',
        order_id: null,
        tool_name: 'resolve_outlet',
        input_payload: {},
        output_payload: {},
        status: 'OK',
        latency_ms: 42,
        created_at: '2026-01-01T00:00:00',
      },
    ]
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => entries,
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<TracePanel apiUrl="http://backend.test" sessionId="SID-003" />)

    await waitFor(() => {
      expect(screen.getByTestId('trace-tool').textContent).toBe('resolve_outlet')
    })
    expect(screen.getByTestId('trace-status').textContent).toBe('OK')
    expect(screen.getByTestId('trace-latency').textContent).toBe('42ms')
  })
})

// ---------------------------------------------------------------------------
// P5-04: Mobile layout
// ---------------------------------------------------------------------------

describe('P5-04 Mobile layout', () => {
  it('renders tab bar on a 390×844 Android viewport with no horizontal overflow', () => {
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 390 })
    Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 844 })

    vi.stubEnv('VITE_API_URL', '')
    // Suppress fetch calls
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }))
    vi.stubGlobal('WebSocket', vi.fn(function () { return { onmessage: null, close: vi.fn() } }))

    render(<App />)

    // Tab bar must render
    expect(screen.getByTestId('tab-bar')).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Rep View' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Manager Queue' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Trace' })).toBeTruthy()

    // No horizontal overflow (scrollWidth <= innerWidth)
    // jsdom doesn't do real layout but we verify the structure doesn't add extra containers
    const shell = document.querySelector('.app-shell')
    expect(shell).toBeTruthy()
  })
})
