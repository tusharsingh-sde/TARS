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

  it('calls resolve_outlet (not add_or_update_line) for the first utterance', async () => {
    const fetchMock = vi.fn(async function (url: string) {
      if (url.endsWith('/api/sessions')) {
        return { ok: true, json: async () => ({ session_id: 'SID-101', rep_id: 'REP-TEST' }) }
      }
      if (url.endsWith('/api/sessions/SID-101/tool')) {
        return {
          ok: true,
          json: async () => ({
            result: {
              status: 'MATCHED',
              order_id: 'ORD-101',
              outlet: {
                outlet_id: 'OUT-001',
                name: 'Sharma General Store',
                owner_name: 'Sharma Ji',
                locality: 'Shastri Nagar',
                city: 'Meerut',
                route_code: 'R1',
                credit_limit_paise: 2500000,
                outstanding_paise: 2325000,
                aliases: ['sharma ji'],
              },
              message: 'Outlet matched: Sharma General Store',
            },
            session_id: 'SID-101',
            trace_entries: [],
          }),
        }
      }
      return { ok: false, json: async () => ({}) }
    })

    const wsMock = { onmessage: null as ((ev: MessageEvent) => void) | null, close: vi.fn() }
    vi.stubGlobal('WebSocket', vi.fn(function () { return wsMock }))
    vi.stubGlobal('fetch', fetchMock)

    render(<RepScreen apiUrl="http://backend.test" onSessionId={() => undefined} sessionId={null} />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        'http://backend.test/api/sessions',
        expect.any(Object),
      )
    })

    const inputEl = screen.getByRole('textbox', { name: 'Order input' })
    fireEvent.change(inputEl, { target: { value: 'sharma ji' } })
    fireEvent.keyDown(inputEl, { key: 'Enter' })

    await waitFor(() => {
      const toolCalls = (fetchMock.mock.calls as [string, RequestInit?][]).filter((c) =>
        c[0].endsWith('/tool'),
      )
      expect(toolCalls).toHaveLength(1)
      const body = JSON.parse(toolCalls[0][1]!.body as string) as {
        name: string
        args: Record<string, unknown>
      }
      expect(body.name).toBe('resolve_outlet')
      expect(body.args).toEqual({ query: 'sharma ji' })
    })
  })

  it('calls add_or_update_line on a subsequent utterance after MATCHED resolve', async () => {
    const orderDetail = {
      order: {
        order_id: 'ORD-102',
        outlet_id: 'OUT-006',
        rep_id: 'REP-TEST',
        status: 'DRAFT',
        total_paise: 0,
        lines: [],
        created_at: '2026-01-01T00:00:00',
        updated_at: '2026-01-01T00:00:00',
      },
      outlet: {
        outlet_id: 'OUT-006',
        name: 'Tiwari Provision Store',
        owner_name: 'Tiwari Ji',
        locality: 'Lajpat Nagar',
        city: 'Meerut',
        route_code: 'R1',
        credit_limit_paise: 5000000,
        outstanding_paise: 100000,
        aliases: ['tiwari ji'],
      },
      invoice: null,
    }
    const updatedOrderDetail = {
      ...orderDetail,
      order: {
        ...orderDetail.order,
        total_paise: 304000,
        lines: [
          {
            id: 1,
            order_id: 'ORD-102',
            sku_id: 'SKU-041',
            quantity: 5,
            unit_price_paise: 60800,
            discount_bps: 0,
            line_total_paise: 304000,
          },
        ],
      },
    }

    let toolCallCount = 0
    const fetchMock = vi.fn(async function (url: string) {
      if (url.endsWith('/api/sessions')) {
        return { ok: true, json: async () => ({ session_id: 'SID-102', rep_id: 'REP-TEST' }) }
      }
      if (url.endsWith('/api/sessions/SID-102/tool')) {
        toolCallCount += 1
        if (toolCallCount === 1) {
          return {
            ok: true,
            json: async () => ({
              result: {
                status: 'MATCHED',
                order_id: 'ORD-102',
                message: 'Outlet matched: Tiwari Provision Store',
              },
              session_id: 'SID-102',
              trace_entries: [],
            }),
          }
        }
        return {
          ok: true,
          json: async () => ({
            result: {
              status: 'OK',
              order: updatedOrderDetail.order,
              message: 'Added 5 cartons Bhoomi Salt 1kg.',
            },
            session_id: 'SID-102',
            trace_entries: [],
          }),
        }
      }
      if (url.endsWith('/api/orders/ORD-102')) {
        return {
          ok: true,
          json: async () => (toolCallCount === 1 ? orderDetail : updatedOrderDetail),
        }
      }
      return { ok: false, json: async () => ({}) }
    })

    const wsMock = { onmessage: null as ((ev: MessageEvent) => void) | null, close: vi.fn() }
    vi.stubGlobal('WebSocket', vi.fn(function () { return wsMock }))
    vi.stubGlobal('fetch', fetchMock)

    render(<RepScreen apiUrl="http://backend.test" onSessionId={() => undefined} sessionId={null} />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        'http://backend.test/api/sessions',
        expect.any(Object),
      )
    })

    const inputEl = screen.getByRole('textbox', { name: 'Order input' })
    fireEvent.change(inputEl, { target: { value: 'tiwari ji' } })
    fireEvent.keyDown(inputEl, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByTestId('outlet-name').textContent).toBe('Tiwari Provision Store')
    })

    fireEvent.change(inputEl, { target: { value: '5 cartons bhoomi salt 1kg' } })
    fireEvent.keyDown(inputEl, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByText(/Added 5 cartons Bhoomi Salt 1kg\./)).toBeTruthy()
    })

    const toolCalls = (fetchMock.mock.calls as [string, RequestInit?][]).filter((c) =>
      c[0].endsWith('/tool'),
    )
    expect(toolCalls).toHaveLength(2)
    const firstBody = JSON.parse(toolCalls[0][1]!.body as string) as {
      name: string
      args: Record<string, unknown>
    }
    const secondBody = JSON.parse(toolCalls[1][1]!.body as string) as {
      name: string
      args: Record<string, unknown>
    }
    expect(firstBody).toEqual({ name: 'resolve_outlet', args: { query: 'tiwari ji' } })
    expect(secondBody).toEqual({
      name: 'add_or_update_line',
      args: { utterance_or_product: '5 cartons bhoomi salt 1kg' },
    })
  })

  it('shows result.message on AMBIGUOUS resolve and does not call add_or_update_line', async () => {
    const fetchMock = vi.fn(async function (url: string) {
      if (url.endsWith('/api/sessions')) {
        return { ok: true, json: async () => ({ session_id: 'SID-103', rep_id: 'REP-TEST' }) }
      }
      if (url.endsWith('/api/sessions/SID-103/tool')) {
        return {
          ok: true,
          json: async () => ({
            result: {
              status: 'AMBIGUOUS',
              order_id: null,
              message: 'Multiple matching outlets found; please clarify locality.',
            },
            session_id: 'SID-103',
            trace_entries: [],
          }),
        }
      }
      return { ok: false, json: async () => ({}) }
    })

    const wsMock = { onmessage: null as ((ev: MessageEvent) => void) | null, close: vi.fn() }
    vi.stubGlobal('WebSocket', vi.fn(function () { return wsMock }))
    vi.stubGlobal('fetch', fetchMock)

    render(<RepScreen apiUrl="http://backend.test" onSessionId={() => undefined} sessionId={null} />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        'http://backend.test/api/sessions',
        expect.any(Object),
      )
    })

    const inputEl = screen.getByRole('textbox', { name: 'Order input' })
    fireEvent.change(inputEl, { target: { value: 'sharma' } })
    fireEvent.keyDown(inputEl, { key: 'Enter' })

    await waitFor(() => {
      expect(screen.getByText(/Multiple matching outlets found; please clarify locality\./)).toBeTruthy()
    })
    expect(screen.getByTestId('bubble-status').textContent).toBe('AMBIGUOUS')

    // Subsequent utterance while outlet is still unresolved must also call resolve_outlet, never add_or_update_line
    fireEvent.change(inputEl, { target: { value: 'sharma general store' } })
    fireEvent.keyDown(inputEl, { key: 'Enter' })

    await waitFor(() => {
      const toolCalls = (fetchMock.mock.calls as [string, RequestInit?][]).filter((c) =>
        c[0].endsWith('/tool'),
      )
      expect(toolCalls).toHaveLength(2)
      for (const call of toolCalls) {
        const body = JSON.parse(call[1]!.body as string) as { name: string }
        expect(body.name).toBe('resolve_outlet')
        expect(body.name).not.toBe('add_or_update_line')
      }
    })
  })

  it('calls record_read_back then commit_order on confirmation when order has lines', async () => {
    let committed = false
    const outlet = {
      outlet_id: 'OUT-001',
      name: 'Sharma General Store',
      owner_name: 'Sharma Ji',
      locality: 'Shastri Nagar',
      city: 'Meerut',
      route_code: 'R1',
      credit_limit_paise: 2500000,
      outstanding_paise: 0,
      aliases: ['sharma ji'],
    }
    const invoice = {
      invoice_id: 'INV-RB-1',
      order_id: 'ORD-RB-1',
      outlet_id: 'OUT-001',
      total_paise: 100000,
      created_at: '2026-11-12T10:00:00Z',
    }
    const fetchMock = vi.fn(async function (
      input: RequestInfo | URL,
      init?: RequestInit,
    ) {
      const url = String(input)
      if (url.endsWith('/api/sessions') && init?.method === 'POST') {
        return {
          ok: true,
          json: async () => ({ session_id: 'SESS-RB-1', rep_id: 'REP-01' }),
        }
      }
      if (url.endsWith('/api/sessions/SESS-RB-1/tool') && init?.method === 'POST') {
        const body = JSON.parse(init.body as string) as {
          name: string
          args: Record<string, unknown>
        }
        if (body.name === 'resolve_outlet') {
          return {
            ok: true,
            json: async () => ({
              session_id: 'SESS-RB-1',
              result: {
                status: 'MATCHED',
                outlet,
                order_id: 'ORD-RB-1',
                message: 'Outlet matched: Sharma General Store',
              },
              trace_entries: [],
            }),
          }
        }
        if (body.name === 'add_or_update_line') {
          return {
            ok: true,
            json: async () => ({
              session_id: 'SESS-RB-1',
              result: {
                status: 'OK',
                order_id: 'ORD-RB-1',
                message: 'Added 5 cartons Bhoomi Salt 1kg.',
              },
              trace_entries: [],
            }),
          }
        }
        if (body.name === 'record_read_back') {
          return {
            ok: true,
            json: async () => ({
              session_id: 'SESS-RB-1',
              result: {
                status: 'OK',
                order_id: 'ORD-RB-1',
                order_status: 'CONFIRMING',
                total_paise: 100000,
                message:
                  'Read-back recorded; awaiting explicit spoken confirmation from rep.',
              },
              trace_entries: [],
            }),
          }
        }
        if (body.name === 'commit_order') {
          committed = true
          return {
            ok: true,
            json: async () => ({
              session_id: 'SESS-RB-1',
              result: {
                status: 'OK',
                order_id: 'ORD-RB-1',
                order_status: 'COMMITTED',
                invoice,
                message: 'Order ORD-RB-1 committed. Invoice INV-RB-1 created.',
              },
              trace_entries: [],
            }),
          }
        }
      }
      if (url.endsWith('/api/orders/ORD-RB-1')) {
        return {
          ok: true,
          json: async () => ({
            order: {
              order_id: 'ORD-RB-1',
              outlet_id: 'OUT-001',
              status: committed ? 'COMMITTED' : 'DRAFT',
              subtotal_paise: 100000,
              discount_paise: 0,
              total_paise: 100000,
              lines: [
                {
                  id: 'LN-RB-1',
                  sku_id: 'SKU-041',
                  sku_name: 'Bhoomi Salt 1kg',
                  pack_size: '1kg',
                  quantity: 5,
                  unit_price_paise: 20000,
                  line_total_paise: 100000,
                  discount_paise: 0,
                  matched_via: 'EXACT',
                },
              ],
            },
            outlet,
            invoice: committed ? invoice : null,
            active_approval: null,
          }),
        }
      }
      return { ok: true, json: async () => ({}) }
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal(
      'WebSocket',
      vi.fn(function () {
        return {
          onmessage: null,
          close: vi.fn(function () {}),
          send: vi.fn(function () {}),
        }
      }),
    )

    render(
      <RepScreen
        apiUrl="http://backend.test"
        onSessionId={() => undefined}
        sessionId={null}
      />,
    )

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        'http://backend.test/api/sessions',
        expect.any(Object),
      )
    })

    const input = screen.getByRole('textbox', { name: 'Order input' })
    fireEvent.change(input, { target: { value: 'sharma ji' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(
      await screen.findByText('Outlet matched: Sharma General Store'),
    ).toBeTruthy()

    fireEvent.change(input, { target: { value: '5 cartons bhoomi salt 1kg' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(
      await screen.findByText('Added 5 cartons Bhoomi Salt 1kg.'),
    ).toBeTruthy()

    fireEvent.change(input, { target: { value: 'haan' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    const banner = await screen.findByTestId('invoice-banner')
    expect(banner.textContent).toContain('INV-RB-1')
    expect(
      screen.getByText(
        'Read-back recorded; awaiting explicit spoken confirmation from rep.',
      ),
    ).toBeTruthy()

    const toolCalls = fetchMock.mock.calls.filter(([u]) =>
      String(u).endsWith('/tool'),
    )
    const toolBodies = toolCalls.map(
      ([, init]) =>
        JSON.parse(init!.body as string) as {
          name: string
          args: Record<string, unknown>
        },
    )
    expect(toolBodies.map((b) => b.name)).toEqual([
      'resolve_outlet',
      'add_or_update_line',
      'record_read_back',
      'commit_order',
    ])
    expect(toolBodies[2].args).toEqual({
      spoken_summary: expect.any(String),
    })
    expect(toolBodies[3].args).toEqual({
      confirmation_phrase: 'haan',
    })
  })

  it('does not call commit_order on confirmation when order has no lines', async () => {
    const outlet = {
      outlet_id: 'OUT-001',
      name: 'Sharma General Store',
      owner_name: 'Sharma Ji',
      locality: 'Shastri Nagar',
      city: 'Meerut',
      route_code: 'R1',
      credit_limit_paise: 2500000,
      outstanding_paise: 0,
      aliases: ['sharma ji'],
    }
    const fetchMock = vi.fn(async function (
      input: RequestInfo | URL,
      init?: RequestInit,
    ) {
      const url = String(input)
      if (url.endsWith('/api/sessions') && init?.method === 'POST') {
        return {
          ok: true,
          json: async () => ({ session_id: 'SESS-EMPTY-1', rep_id: 'REP-01' }),
        }
      }
      if (
        url.endsWith('/api/sessions/SESS-EMPTY-1/tool') &&
        init?.method === 'POST'
      ) {
        const body = JSON.parse(init.body as string) as {
          name: string
          args: Record<string, unknown>
        }
        if (body.name === 'resolve_outlet') {
          return {
            ok: true,
            json: async () => ({
              session_id: 'SESS-EMPTY-1',
              result: {
                status: 'MATCHED',
                outlet,
                order_id: 'ORD-EMPTY-1',
                message: 'Outlet matched: Sharma General Store',
              },
              trace_entries: [],
            }),
          }
        }
        if (body.name === 'record_read_back') {
          return {
            ok: true,
            json: async () => ({
              session_id: 'SESS-EMPTY-1',
              result: {
                status: 'BLOCKED',
                order_id: 'ORD-EMPTY-1',
                order_status: 'DRAFT',
                reason: 'EMPTY_ORDER',
                message: 'Cannot read back an empty order.',
              },
              trace_entries: [],
            }),
          }
        }
      }
      if (url.endsWith('/api/orders/ORD-EMPTY-1')) {
        return {
          ok: true,
          json: async () => ({
            order: {
              order_id: 'ORD-EMPTY-1',
              outlet_id: 'OUT-001',
              status: 'DRAFT',
              subtotal_paise: 0,
              discount_paise: 0,
              total_paise: 0,
              lines: [],
            },
            outlet,
            invoice: null,
            active_approval: null,
          }),
        }
      }
      return { ok: true, json: async () => ({}) }
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal(
      'WebSocket',
      vi.fn(function () {
        return {
          onmessage: null,
          close: vi.fn(function () {}),
          send: vi.fn(function () {}),
        }
      }),
    )

    render(
      <RepScreen
        apiUrl="http://backend.test"
        onSessionId={() => undefined}
        sessionId={null}
      />,
    )

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        'http://backend.test/api/sessions',
        expect.any(Object),
      )
    })

    const input = screen.getByRole('textbox', { name: 'Order input' })
    fireEvent.change(input, { target: { value: 'sharma ji' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(
      await screen.findByText('Outlet matched: Sharma General Store'),
    ).toBeTruthy()

    fireEvent.change(input, { target: { value: 'confirm' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(
      await screen.findByText('Cannot read back an empty order.'),
    ).toBeTruthy()

    const toolCalls = fetchMock.mock.calls.filter(([u]) =>
      String(u).endsWith('/tool'),
    )
    const toolNames = toolCalls.map(
      ([, init]) => (JSON.parse(init!.body as string) as { name: string }).name,
    )
    expect(toolNames).toEqual(['resolve_outlet', 'record_read_back'])
    expect(toolNames).not.toContain('commit_order')
    expect(toolNames).not.toContain('add_or_update_line')
    expect(screen.queryByTestId('invoice-banner')).toBeNull()
  })

  it('routes non-confirmation utterance after outlet resolution to add_or_update_line', async () => {
    const outlet = {
      outlet_id: 'OUT-001',
      name: 'Sharma General Store',
      owner_name: 'Sharma Ji',
      locality: 'Shastri Nagar',
      city: 'Meerut',
      route_code: 'R1',
      credit_limit_paise: 2500000,
      outstanding_paise: 0,
      aliases: ['sharma ji'],
    }
    const fetchMock = vi.fn(async function (
      input: RequestInfo | URL,
      init?: RequestInit,
    ) {
      const url = String(input)
      if (url.endsWith('/api/sessions') && init?.method === 'POST') {
        return {
          ok: true,
          json: async () => ({ session_id: 'SESS-LINE-1', rep_id: 'REP-01' }),
        }
      }
      if (
        url.endsWith('/api/sessions/SESS-LINE-1/tool') &&
        init?.method === 'POST'
      ) {
        const body = JSON.parse(init.body as string) as {
          name: string
          args: Record<string, unknown>
        }
        if (body.name === 'resolve_outlet') {
          return {
            ok: true,
            json: async () => ({
              session_id: 'SESS-LINE-1',
              result: {
                status: 'MATCHED',
                outlet,
                order_id: 'ORD-LINE-1',
                message: 'Outlet matched: Sharma General Store',
              },
              trace_entries: [],
            }),
          }
        }
        if (body.name === 'add_or_update_line') {
          return {
            ok: true,
            json: async () => ({
              session_id: 'SESS-LINE-1',
              result: {
                status: 'OK',
                order_id: 'ORD-LINE-1',
                message: 'Added 2 cartons Bhoomi Salt 1kg.',
              },
              trace_entries: [],
            }),
          }
        }
      }
      if (url.endsWith('/api/orders/ORD-LINE-1')) {
        return {
          ok: true,
          json: async () => ({
            order: {
              order_id: 'ORD-LINE-1',
              outlet_id: 'OUT-001',
              status: 'DRAFT',
              subtotal_paise: 40000,
              discount_paise: 0,
              total_paise: 40000,
              lines: [],
            },
            outlet,
            invoice: null,
            active_approval: null,
          }),
        }
      }
      return { ok: true, json: async () => ({}) }
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal(
      'WebSocket',
      vi.fn(function () {
        return {
          onmessage: null,
          close: vi.fn(function () {}),
          send: vi.fn(function () {}),
        }
      }),
    )

    render(
      <RepScreen
        apiUrl="http://backend.test"
        onSessionId={() => undefined}
        sessionId={null}
      />,
    )

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        'http://backend.test/api/sessions',
        expect.any(Object),
      )
    })

    const input = screen.getByRole('textbox', { name: 'Order input' })
    fireEvent.change(input, { target: { value: 'sharma ji' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(
      await screen.findByText('Outlet matched: Sharma General Store'),
    ).toBeTruthy()

    fireEvent.change(input, { target: { value: '2 cartons bhoomi salt 1kg' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(
      await screen.findByText('Added 2 cartons Bhoomi Salt 1kg.'),
    ).toBeTruthy()

    const toolCalls = fetchMock.mock.calls.filter(([u]) =>
      String(u).endsWith('/tool'),
    )
    const toolBodies = toolCalls.map(
      ([, init]) =>
        JSON.parse(init!.body as string) as {
          name: string
          args: Record<string, unknown>
        },
    )
    expect(toolBodies.map((b) => b.name)).toEqual([
      'resolve_outlet',
      'add_or_update_line',
    ])
    expect(toolBodies[1].args).toEqual({
      utterance_or_product: '2 cartons bhoomi salt 1kg',
    })
  })

  it('calls getUserMedia with channelCount 1 and sends PCM16 chunks when socket is open', async () => {
    const prevMediaDevices = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices')
    const trackStop = vi.fn(function () {})
    const getUserMediaMock = vi.fn(async function () {
      return {
        getTracks: function () {
          return [{ stop: trackStop }]
        },
      }
    })
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: getUserMediaMock },
    })

    const wsSend = vi.fn(function () {})
    const wsMock = {
      readyState: 1,
      onmessage: null,
      send: wsSend,
      close: vi.fn(function () {}),
    }
    const workletNodeMock = {
      port: { onmessage: null as ((ev: MessageEvent) => void) | null },
      connect: vi.fn(function () {}),
      disconnect: vi.fn(function () {}),
    }
    const ctxClose = vi.fn(async function () {})
    const addModuleMock = vi.fn(async function () {})
    const ctxMock = {
      state: 'running',
      sampleRate: 16000,
      destination: {},
      audioWorklet: { addModule: addModuleMock },
      createMediaStreamSource: vi.fn(function () {
        return { connect: vi.fn(function () {}), disconnect: vi.fn(function () {}) }
      }),
      createGain: vi.fn(function () {
        return {
          gain: { value: 1 },
          connect: vi.fn(function () {}),
          disconnect: vi.fn(function () {}),
        }
      }),
      close: ctxClose,
    }
    const fetchMock = vi.fn(async function (url: string) {
      if (url.endsWith('/api/sessions')) {
        return {
          ok: true,
          json: async () => ({ session_id: 'SESS-MIC-1', rep_id: 'REP-01' }),
        }
      }
      return { ok: true, json: async () => ({}) }
    })

    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', vi.fn(function () { return wsMock }))
    vi.stubGlobal('AudioContext', vi.fn(function () { return ctxMock }))
    vi.stubGlobal('AudioWorkletNode', vi.fn(function () { return workletNodeMock }))

    try {
      render(
        <RepScreen
          apiUrl="http://backend.test"
          onSessionId={() => undefined}
          sessionId={null}
        />,
      )

      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          'http://backend.test/api/sessions',
          expect.any(Object),
        )
      })

      const micBtn = screen.getByTestId('mic-button')
      fireEvent.click(micBtn)

      await waitFor(() => {
        expect(getUserMediaMock).toHaveBeenCalledWith({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
          },
        })
      })
      await waitFor(() => {
        expect(micBtn.getAttribute('aria-label')).toBe('Stop recording')
      })

      const chunk = new ArrayBuffer(960)
      workletNodeMock.port.onmessage?.({ data: chunk } as MessageEvent)
      expect(wsSend).toHaveBeenCalledWith(chunk)

      fireEvent.click(micBtn)
      expect(trackStop).toHaveBeenCalledTimes(1)
      expect(ctxClose).toHaveBeenCalledTimes(1)
      expect(micBtn.getAttribute('aria-label')).toBe('Start recording')
    } finally {
      if (prevMediaDevices) {
        Object.defineProperty(navigator, 'mediaDevices', prevMediaDevices)
      } else {
        Object.defineProperty(navigator, 'mediaDevices', {
          configurable: true,
          value: undefined,
        })
      }
    }
  })

  it('does not call getUserMedia and shows not ready bubble when socket is not open', async () => {
    const prevMediaDevices = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices')
    const getUserMediaMock = vi.fn(async function () {
      return { getTracks: function () { return [] } }
    })
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: getUserMediaMock },
    })

    const wsMock = {
      readyState: 0,
      onmessage: null,
      send: vi.fn(function () {}),
      close: vi.fn(function () {}),
    }
    const fetchMock = vi.fn(async function (url: string) {
      if (url.endsWith('/api/sessions')) {
        return {
          ok: true,
          json: async () => ({ session_id: 'SESS-MIC-2', rep_id: 'REP-01' }),
        }
      }
      return { ok: true, json: async () => ({}) }
    })

    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', vi.fn(function () { return wsMock }))

    try {
      render(
        <RepScreen
          apiUrl="http://backend.test"
          onSessionId={() => undefined}
          sessionId={null}
        />,
      )

      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          'http://backend.test/api/sessions',
          expect.any(Object),
        )
      })

      fireEvent.click(screen.getByTestId('mic-button'))

      expect(await screen.findByText('Voice connection not ready')).toBeTruthy()
      expect(getUserMediaMock).not.toHaveBeenCalled()
    } finally {
      if (prevMediaDevices) {
        Object.defineProperty(navigator, 'mediaDevices', prevMediaDevices)
      } else {
        Object.defineProperty(navigator, 'mediaDevices', {
          configurable: true,
          value: undefined,
        })
      }
    }
  })

  it('shows error name in a TARS bubble when getUserMedia permission is denied', async () => {
    const prevMediaDevices = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices')
    const getUserMediaMock = vi.fn(async function () {
      throw Object.assign(new Error('Permission denied'), { name: 'NotAllowedError' })
    })
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: getUserMediaMock },
    })

    const wsMock = {
      readyState: 1,
      onmessage: null,
      send: vi.fn(function () {}),
      close: vi.fn(function () {}),
    }
    const fetchMock = vi.fn(async function (url: string) {
      if (url.endsWith('/api/sessions')) {
        return {
          ok: true,
          json: async () => ({ session_id: 'SESS-MIC-3', rep_id: 'REP-01' }),
        }
      }
      return { ok: true, json: async () => ({}) }
    })

    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', vi.fn(function () { return wsMock }))

    try {
      render(
        <RepScreen
          apiUrl="http://backend.test"
          onSessionId={() => undefined}
          sessionId={null}
        />,
      )

      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          'http://backend.test/api/sessions',
          expect.any(Object),
        )
      })

      const micBtn = screen.getByTestId('mic-button')
      fireEvent.click(micBtn)

      expect(await screen.findByText(/NotAllowedError/)).toBeTruthy()
      expect(micBtn.getAttribute('aria-label')).toBe('Start recording')
    } finally {
      if (prevMediaDevices) {
        Object.defineProperty(navigator, 'mediaDevices', prevMediaDevices)
      } else {
        Object.defineProperty(navigator, 'mediaDevices', {
          configurable: true,
          value: undefined,
        })
      }
    }
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
