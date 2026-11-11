// Shared TypeScript types mirroring backend Pydantic DTOs.

export type OrderStatus =
  | 'DRAFT'
  | 'CONFIRMING'
  | 'PENDING_APPROVAL'
  | 'COMMITTED'
  | 'REJECTED'

export type ApprovalStatus = 'PENDING' | 'APPROVED' | 'REJECTED'
export type TraceStatus = 'OK' | 'BLOCKED' | 'ERROR'

export interface OutletDTO {
  outlet_id: string
  name: string
  owner_name: string
  locality: string
  city: string
  route_code: string
  credit_limit_paise: number
  outstanding_paise: number
  aliases: string[]
}

export interface SkuDTO {
  sku_id: string
  brand: string
  product: string
  pack_size: string
  unit: string
  mrp_paise: number
  base_price_paise: number
  stock_units: number
  aliases: string[]
}

export interface SchemeDTO {
  scheme_id: string
  sku_id: string
  min_qty: number
  discount_bps: number
  valid_from: string
  valid_to: string
  description: string
}

export interface OrderLineDTO {
  id: number
  order_id: string
  sku_id: string
  quantity: number
  unit_price_paise: number
  discount_bps: number
  line_total_paise: number
}

export interface OrderDTO {
  order_id: string
  outlet_id: string
  rep_id: string
  status: OrderStatus
  total_paise: number
  lines: OrderLineDTO[]
  created_at: string
  updated_at: string
}

export interface InvoiceDTO {
  invoice_id: string
  order_id: string
  total_paise: number
  created_at: string
}

export interface ApprovalDTO {
  request_id: string
  order_id: string
  reason_code: string
  status: ApprovalStatus
  decided_by: string | null
  decided_at: string | null
  created_at: string
}

export interface ApprovalWithSummary {
  approval: ApprovalDTO
  order_total_paise: number
  outlet_name: string
  outlet_id: string
}

export interface TraceEntry {
  event_id: number
  session_id: string
  order_id: string | null
  tool_name: string
  input_payload: Record<string, unknown>
  output_payload: Record<string, unknown>
  status: TraceStatus
  latency_ms: number
  created_at: string
}

/** Convert integer paise to a ₹-prefixed string with two decimal places. */
export function paisaToRupees(paise: number): string {
  const rupees = paise / 100
  return `₹${rupees.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

