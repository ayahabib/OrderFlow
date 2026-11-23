import { supabase } from './supabase'

const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** Turn a FastAPI error body into one readable Arabic message. */
function errorMessage(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // Pydantic validation errors: "Value error, <msg>" -> "<msg>"
    return detail
      .map((d: { msg?: string }) => (d.msg ?? '').replace(/^Value error, /, ''))
      .filter(Boolean)
      .join('، ')
  }
  if (status === 401) return 'انتهت الجلسة، سجّل الدخول من جديد'
  return 'صار خطأ، حاول مرة ثانية'
}

/** fetch() against the backend with the current Supabase access token. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token

  let res: Response
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: {
        // FormData (file upload) sets its own multipart Content-Type.
        ...(init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init.headers,
      },
    })
  } catch {
    throw new ApiError(0, 'ما قدرنا نوصل للسيرفر. تأكد إنه شغّال.')
  }

  const body = res.status === 204 ? null : await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(res.status, errorMessage(res.status, body))
  return body as T
}

export type Me = { user_id: string; email: string | null; store_id: string | null }

export type Store = {
  id: string
  name: string
  phone: string | null
  currency: string
  timezone: string
  created_at: string
}

export type StoreInput = { name: string; phone: string | null; currency: string }

export const getMe = () => api<Me>('/api/me')
export const getStore = () => api<Store>('/api/store')
export const createStore = (input: StoreInput) =>
  api<Store>('/api/store', { method: 'POST', body: JSON.stringify(input) })
export const updateStore = (input: Partial<StoreInput>) =>
  api<Store>('/api/store', { method: 'PATCH', body: JSON.stringify(input) })

// --- products & variants ---

export type Variant = {
  id: string
  name: string
  options: Record<string, string>
  sku: string | null
  price_override: string | null
  effective_price: string
  stock_quantity: number
  low_stock_threshold: number
  is_active: boolean
  offer_price: string | null // price after the best live offer, computed by the backend
  offer_name: string | null
}

export type Product = {
  id: string
  name: string
  category: string | null
  price: string
  is_active: boolean
  variants: Variant[]
}

export const listProducts = () => api<Product[]>('/api/products')
export const updateProductPrice = (id: string, price: string) =>
  api<Product>(`/api/products/${id}`, { method: 'PATCH', body: JSON.stringify({ price }) })
export const updateVariant = (id: string, changes: { stock_quantity?: number; price_override?: string | null; low_stock_threshold?: number }) =>
  api<Variant>(`/api/variants/${id}`, { method: 'PATCH', body: JSON.stringify(changes) })

export type VariantInput = {
  color?: string | null
  size?: string | null
  quantity: number
  price_override?: string | null
  sku?: string | null
}
export type ProductInput = { name: string; price: string; category?: string | null; variants: VariantInput[] }

export const createProduct = (input: ProductInput) =>
  api<Product>('/api/products', { method: 'POST', body: JSON.stringify(input) })
export const addVariant = (productId: string, input: VariantInput) =>
  api<Product>(`/api/products/${productId}/variants`, { method: 'POST', body: JSON.stringify(input) })
export const deleteProduct = (id: string) => api<null>(`/api/products/${id}`, { method: 'DELETE' })
export const deleteVariant = (id: string) => api<null>(`/api/variants/${id}`, { method: 'DELETE' })

// --- inventory import ---

export type RowError = { row: number; message: string }

export type ImportPreview = {
  total_rows: number
  valid_rows: number
  errors: RowError[]
  products: {
    name: string
    price: string
    category: string | null
    is_new: boolean
    variants: {
      row: number
      name: string
      quantity: number
      current_quantity: number | null
      price_override: string | null
      sku: string | null
    }[]
  }[]
}

export type ImportResult = {
  products_created: number
  products_updated: number
  variants_created: number
  variants_updated: number
  movements: number
  skipped_rows: RowError[]
}

function fileForm(file: File): FormData {
  const form = new FormData()
  form.append('file', file)
  return form
}

export const previewImport = (file: File) =>
  api<ImportPreview>('/api/products/import/preview', { method: 'POST', body: fileForm(file) })
export const confirmImport = (file: File) =>
  api<ImportResult>('/api/products/import/confirm', { method: 'POST', body: fileForm(file) })

// --- offers ---

export type OfferKind = 'percent_off' | 'fixed_price' | 'free_delivery'
export type OfferStatus = 'live' | 'scheduled' | 'ended' | 'paused'

export type Offer = {
  id: string
  name: string
  kind: OfferKind
  product_id: string | null
  variant_id: string | null
  product_name: string | null
  variant_name: string | null
  percent: string | null
  fixed_price: string | null
  min_order_total: string | null
  starts_at: string | null
  ends_at: string | null
  is_active: boolean
  status: OfferStatus
}

export type OfferInput = {
  name: string
  kind: OfferKind
  product_id?: string | null
  variant_id?: string | null
  percent?: string | null
  fixed_price?: string | null
  min_order_total?: string | null
  starts_at?: string | null
  ends_at?: string | null
}

export type OfferChanges = Partial<Pick<Offer, 'name' | 'percent' | 'fixed_price' | 'min_order_total' | 'starts_at' | 'ends_at' | 'is_active'>>

export const listOffers = () => api<Offer[]>('/api/offers')
export const createOffer = (input: OfferInput) =>
  api<Offer>('/api/offers', { method: 'POST', body: JSON.stringify(input) })
export const updateOffer = (id: string, changes: OfferChanges) =>
  api<Offer>(`/api/offers/${id}`, { method: 'PATCH', body: JSON.stringify(changes) })
export const deleteOffer = (id: string) => api<null>(`/api/offers/${id}`, { method: 'DELETE' })

// --- delivery zones ---

export type Zone = { id: string; name: string; fee: string; is_active: boolean }

export const listZones = () => api<Zone[]>('/api/delivery-zones')
export const createZone = (input: { name: string; fee: string }) =>
  api<Zone>('/api/delivery-zones', { method: 'POST', body: JSON.stringify(input) })
export const updateZone = (id: string, changes: Partial<Omit<Zone, 'id'>>) =>
  api<Zone>(`/api/delivery-zones/${id}`, { method: 'PATCH', body: JSON.stringify(changes) })
export const deleteZone = (id: string) => api<null>(`/api/delivery-zones/${id}`, { method: 'DELETE' })

// --- orders ---

export type OrderStatus = 'new' | 'in_progress' | 'delivered' | 'cancelled'

export type Order = {
  id: string
  order_number: number
  status: OrderStatus
  customer_name: string | null
  customer_phone: string | null
  channel: string
  delivery_address: string | null
  zone_name: string | null
  delivery_fee: string
  subtotal: string
  total: string
  notes: string | null
  items: { product_name: string; variant_name: string | null; quantity: number; unit_price: string; list_price: string | null; notes: string | null }[]
  allowed_next: OrderStatus[]
  created_at: string
  updated_at: string
}

export type OrderFilters = { status?: OrderStatus | ''; from?: string; to?: string; q?: string }

export function listOrders(filters: OrderFilters = {}) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value)
  const qs = params.toString()
  return api<Order[]>(`/api/orders${qs ? `?${qs}` : ''}`)
}

export const updateOrderStatus = (id: string, status: OrderStatus) =>
  api<Order>(`/api/orders/${id}/status`, { method: 'PATCH', body: JSON.stringify({ status }) })

// --- owner notifications (Telegram) ---

export type TelegramStatus = { available: boolean; linked: boolean }

export const getTelegramStatus = () => api<TelegramStatus>('/api/store/telegram')
export const createTelegramLink = () =>
  api<{ url: string; expires_in: number }>('/api/store/telegram/link', { method: 'POST' })
export const verifyTelegramLink = () => api<TelegramStatus>('/api/store/telegram/verify', { method: 'POST' })
export const sendTelegramTest = () => api<null>('/api/store/telegram/test', { method: 'POST' })
export const unlinkTelegram = () => api<TelegramStatus>('/api/store/telegram', { method: 'DELETE' })

// --- conversations (owner side) ---

export type ConversationMode = 'bot' | 'paused' | 'waiting_owner' | 'owner' | 'closed'
export type ConversationView = '' | 'waiting' | 'owner' | 'bot' | 'closed'

export type ConversationSummary = {
  id: string
  channel: string
  mode: ConversationMode
  customer_name: string | null
  customer_phone: string | null
  last_message: string | null
  last_sender: string | null
  open_escalations: number
  last_message_at: string
}

export type ConversationMessage = { id: string; sender: 'customer' | 'bot' | 'owner'; content: string; created_at: string }
export type ConversationEscalation = {
  id: string
  rule: string | null
  question: string
  status: string
  owner_answer: string | null
  save_to_kb: boolean | null
  created_at: string
}
export type OrderDraft = {
  items: { product_name: string; variant_name: string; quantity: number }[]
  unresolved: number
  address: string | null
  zone_name: string | null
  phone: string | null
  waiting_for: string | null
}

export type ConversationDetail = {
  id: string
  channel: string
  mode: ConversationMode
  customer_name: string | null
  customer_phone: string | null
  can_reply: boolean
  draft: OrderDraft | null
  escalations: ConversationEscalation[]
  messages: ConversationMessage[]
}

export function listConversations(view: ConversationView, q: string) {
  const params = new URLSearchParams()
  if (view) params.set('view', view)
  if (q) params.set('q', q)
  const qs = params.toString()
  return api<ConversationSummary[]>(`/api/conversations${qs ? `?${qs}` : ''}`)
}
export const getConversation = (id: string) => api<ConversationDetail>(`/api/conversations/${id}`)
export const pauseConversation = (id: string) =>
  api<ConversationDetail>(`/api/conversations/${id}/pause`, { method: 'POST' })
export const resumeConversation = (id: string, keepOrder: boolean) =>
  api<ConversationDetail>(`/api/conversations/${id}/resume`, { method: 'POST', body: JSON.stringify({ keep_order: keepOrder }) })
export const replyToConversation = (id: string, text: string) =>
  api<ConversationDetail>(`/api/conversations/${id}/reply`, { method: 'POST', body: JSON.stringify({ text }) })

// --- customer channels ---

export type TelegramChannelStatus = {
  connected: boolean
  bot_username: string | null
  webhook_ok: boolean | null
  webhook_error: string | null
}

export const getTelegramChannel = () => api<TelegramChannelStatus>('/api/channels/telegram')
export const connectTelegramChannel = (botToken: string) =>
  api<TelegramChannelStatus>('/api/channels/telegram', { method: 'POST', body: JSON.stringify({ bot_token: botToken }) })
export const disconnectTelegramChannel = () => api<TelegramChannelStatus>('/api/channels/telegram', { method: 'DELETE' })

// --- knowledge base (store info the bot answers from) ---

export type KnowledgeEntry = {
  id: string
  question: string | null
  answer: string
  source: 'upload' | 'owner_answer' | 'manual'
  is_active: boolean
  created_at: string
}
export type KnowledgeHit = { id: string; question: string | null; answer: string; similarity: number }

export const listKnowledge = () => api<KnowledgeEntry[]>('/api/knowledge')
export const addKnowledge = (input: { question: string | null; answer: string }) =>
  api<KnowledgeEntry>('/api/knowledge', { method: 'POST', body: JSON.stringify(input) })
export const updateKnowledge = (id: string, changes: Partial<Pick<KnowledgeEntry, 'question' | 'answer' | 'is_active'>>) =>
  api<KnowledgeEntry>(`/api/knowledge/${id}`, { method: 'PATCH', body: JSON.stringify(changes) })
export const deleteKnowledge = (id: string) => api<null>(`/api/knowledge/${id}`, { method: 'DELETE' })
export function uploadKnowledge(file: File) {
  const form = new FormData()
  form.append('file', file)
  return api<KnowledgeEntry[]>('/api/knowledge/upload', { method: 'POST', body: form })
}
export const searchKnowledge = (query: string) =>
  api<KnowledgeHit[]>('/api/knowledge/search', { method: 'POST', body: JSON.stringify({ query }) })

// --- escalated questions (2.3) ---

export const answerEscalation = (id: string, input: {
  answer: string
  save_to_kb: boolean
  then: 'bot' | 'owner'
  kb_question?: string
  kb_answer?: string
}) =>
  api<ConversationDetail>(`/api/escalations/${id}/answer`, { method: 'POST', body: JSON.stringify(input) })

// --- sales dashboard (3.1) ---

export type SalesReport = {
  currency: string
  start: string
  end: string
  orders_count: number
  revenue: string
  average_order: string | null
  new_orders: number
  in_progress_orders: number
  days: { day: string; orders_count: number; revenue: string }[]
  top_products: { product_name: string; quantity: number; revenue: string }[]
}

export const getSalesReport = (days: number) => api<SalesReport>(`/api/reports/sales?days=${days}`)

// --- stock page (3.2) ---

export type LowStockItem = {
  variant_id: string
  product_id: string
  product_name: string
  variant_name: string
  stock_quantity: number
  low_stock_threshold: number
}
export type ProductStock = { product_id: string; name: string; variants_count: number; total_stock: number }

export const getLowStock = () => api<LowStockItem[]>('/api/reports/low-stock')
export const getStockByProduct = () => api<ProductStock[]>('/api/reports/stock')

// --- owner stock chat (3.3): propose, then confirm ---

export type PendingStockAction = {
  id: string
  product_name: string
  variant_name: string
  old_stock: number
  new_stock: number
  expires_at: string
}
export type OwnerChatReply = { reply: string; action: PendingStockAction | null }
export type ConfirmedStockAction = { reply: string; product_name: string; variant_name: string; old_stock: number; new_stock: number }

export const sendOwnerChat = (text: string) =>
  api<OwnerChatReply>('/api/owner-chat', { method: 'POST', body: JSON.stringify({ text }) })
export const confirmOwnerAction = (id: string) =>
  api<ConfirmedStockAction>(`/api/owner-chat/confirm/${id}`, { method: 'POST' })
export const cancelOwnerAction = (id: string) =>
  api<null>(`/api/owner-chat/cancel/${id}`, { method: 'POST' })

// --- stock run-out forecast (3.4) ---

export type ForecastItem = {
  variant_id: string
  product_id: string
  product_name: string
  variant_name: string
  stock_quantity: number
  sold: number
  daily_rate: string
  days_left: number
  run_out_date: string
}
export type ForecastReport = { window_days: number; within_days: number; items: ForecastItem[] }

export const getForecast = (days: number) => api<ForecastReport>(`/api/reports/forecast?days=${days}`)
