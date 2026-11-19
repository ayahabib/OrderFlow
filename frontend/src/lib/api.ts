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
        'Content-Type': 'application/json',
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
