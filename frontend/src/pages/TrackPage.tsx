import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'

const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'
const REFRESH_MS = 60_000
const num = new Intl.NumberFormat('ar-u-nu-latn', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const dateFmt = new Intl.DateTimeFormat('ar-u-nu-latn', { weekday: 'long', day: 'numeric', month: 'long', hour: 'numeric', minute: '2-digit' })

type Tracked = {
  store_name: string
  currency: string
  order_number: number
  status: 'new' | 'in_progress' | 'delivered' | 'cancelled'
  steps: string[]
  zone_name: string | null
  delivery_fee: string
  total: string
  items: { product_name: string; variant_name: string | null; quantity: number }[]
  created_at: string
  updated_at: string
}

const STEP_LABEL: Record<string, string> = { new: 'استلمنا الطلب', in_progress: 'قيد التجهيز والتوصيل', delivered: 'تم التوصيل' }

/** Public order tracking (task 3.6): no login, the random token in the link is the only key. */
export default function TrackPage() {
  const { token = '' } = useParams()
  const [order, setOrder] = useState<Tracked | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const res = await fetch(`${BASE_URL}/api/track/${encodeURIComponent(token)}`)
        if (!res.ok) throw new Error(res.status === 404 || res.status === 422 ? 'ما لقينا هالطلب. تأكد من الرابط.' : 'صار خطأ، جرّب كمان شوي.')
        const data = (await res.json()) as Tracked
        if (alive) { setOrder(data); setError(null) }
      } catch (e) {
        if (alive) setError(e instanceof TypeError ? 'ما قدرنا نوصل للسيرفر، جرّب كمان شوي.' : (e as Error).message)
      }
    }
    load()
    const timer = window.setInterval(load, REFRESH_MS)   // the status updates without reloading
    return () => { alive = false; window.clearInterval(timer) }
  }, [token])

  if (error) return <main className="track-page"><p className="msg error" role="alert">{error}</p></main>
  if (!order) return <main className="track-page"><p className="muted">جاري التحميل...</p></main>

  const current = order.steps.indexOf(order.status)
  const finished = order.status === 'delivered'   // the last step is done, not in progress
  const money = (v: string) => `${num.format(Number(v))} ${order.currency}`

  return (
    <main className="track-page">
      <div className="card track-card">
        <p className="muted small">{order.store_name}</p>
        <h1>طلب رقم {order.order_number}</h1>

        {order.status === 'cancelled' ? (
          <p className="msg error">هالطلب انلغى. إذا عندك سؤال احكي مع المتجر.</p>
        ) : (
          <ol className="track-steps" aria-label="حالة الطلب">
            {order.steps.map((s, i) => (
              <li key={s} className={i < current || finished ? 'done' : i === current ? 'current' : ''} aria-current={i === current ? 'step' : undefined}>
                {STEP_LABEL[s] ?? s}
              </li>
            ))}
          </ol>
        )}
        <p className="muted small">آخر تحديث: {dateFmt.format(new Date(order.updated_at))}</p>

        <h2>المنتجات</h2>
        <ul className="track-items">
          {order.items.map((i, n) => (
            <li key={n}>{i.product_name}{i.variant_name ? ` (${i.variant_name})` : ''} × {i.quantity}</li>
          ))}
        </ul>
        <dl className="track-totals">
          <dt>التوصيل{order.zone_name ? ` (${order.zone_name})` : ''}</dt><dd>{money(order.delivery_fee)}</dd>
          <dt>الإجمالي</dt><dd><strong>{money(order.total)}</strong></dd>
        </dl>
        <p className="muted small">تاريخ الطلب: {dateFmt.format(new Date(order.created_at))}</p>
      </div>
    </main>
  )
}
