// Web chat simulator client. Plays the customer, so it does NOT send the owner's login token:
// it uses the session token from /webhooks/web/{store}/sessions instead.

const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'

export type ChatMessage = {
  id: string
  sender: 'customer' | 'bot' | 'owner'
  content: string
  buttons: { id: string; title: string }[]
  created_at: string
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE_URL}${path}`, init)
  } catch {
    throw new Error('ما قدرنا نوصل للسيرفر')
  }
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail
    const err = new Error(typeof detail === 'string' ? detail : 'صار خطأ') as Error & { status?: number }
    err.status = res.status
    throw err
  }
  return body as T
}

export const startSession = (storeId: string) =>
  call<{ session_token: string; customer_id: string }>(`/webhooks/web/${storeId}/sessions`, { method: 'POST' })

export const sendMessage = (storeId: string, token: string, text: string, clientMessageId: string, customerName: string | null) =>
  call<{ status: 'received' | 'duplicate' }>(`/webhooks/web/${storeId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Session-Token': token },
    body: JSON.stringify({ text, client_message_id: clientMessageId, customer_name: customerName }),
  })

export const fetchMessages = (storeId: string, token: string) =>
  call<ChatMessage[]>(`/webhooks/web/${storeId}/messages`, { headers: { 'X-Session-Token': token } })

export type ConversationState = { status: string; bot_replies: boolean }

export const fetchConversationState = (storeId: string, token: string) =>
  call<ConversationState>(`/webhooks/web/${storeId}/conversation`, { headers: { 'X-Session-Token': token } })
