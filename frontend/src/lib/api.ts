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
export const updateVariant = (id: string, changes: { stock_quantity?: number; price_override?: string | null }) =>
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
