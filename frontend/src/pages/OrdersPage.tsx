import { useCallback, useEffect, useState } from 'react'
import { listOrders, updateOrderStatus, type Order, type OrderFilters, type OrderStatus } from '../lib/api'
import IncompleteOrders from '../components/IncompleteOrders'
import { useStore } from '../store/StoreProvider'
import { isClosedWithoutSale, ORDER_STATUS as STATUS } from '../lib/orderStatus'

// Button text for each move the backend allows.
const ACTION: Record<OrderStatus, string> = {
  new: '',
  in_progress: 'قيد التجهيز',
  shipped: 'تم الشحن',
  delivered: 'تم التوصيل',
  cancelled: 'إلغاء',
  returned: 'إرجاع',
}

const REFRESH_MS = 10_000   // 3.15; skipped while the tab is hidden
const dateFmt = new Intl.DateTimeFormat('ar-u-nu-latn', { dateStyle: 'medium', timeStyle: 'short' })

function itemLabel(i: Order['items'][number]) {
  return i.variant_name && i.variant_name !== 'الافتراضي' ? `${i.product_name} (${i.variant_name})` : i.product_name
}

export default function OrdersPage() {
  const { store } = useStore()
  const [orders, setOrders] = useState<Order[] | null>(null)
  const [filters, setFilters] = useState<OrderFilters>({ status: '', from: '', to: '', q: '' })
  const [query, setQuery] = useState('')  // typed text; applied after a short pause
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setOrders(await listOrders(filters))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [filters])

  useEffect(() => {
    load()
    const timer = window.setInterval(() => { if (!document.hidden) load() }, REFRESH_MS)  // new bot orders show up without reloading
    return () => window.clearInterval(timer)
  }, [load])

  useEffect(() => {
    const t = window.setTimeout(() => setFilters((f) => ({ ...f, q: query.trim() })), 300)
    return () => window.clearTimeout(t)
  }, [query])

  async function move(o: Order, next: OrderStatus) {
    if (next === 'cancelled' &&
      !window.confirm(`إلغاء الطلب #${o.order_number}؟\nالكميات رح ترجع للمخزون، وما بتقدر ترجّع الطلب بعدها.`)) return
    if (next === 'returned' &&
      !window.confirm(`تسجيل الطلب #${o.order_number} كمرتجع؟\nالكميات رح ترجع للمخزون، والطلب بيطلع من المبيعات. ما بتقدر تتراجع بعدها.`)) return
    setActionError(null)
    setBusyId(o.id)
    try {
      const updated = await updateOrderStatus(o.id, next)
      setOrders((list) => list?.map((x) => (x.id === updated.id ? updated : x)) ?? null)
    } catch (e) {
      setActionError((e as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const set = (patch: Partial<OrderFilters>) => setFilters((f) => ({ ...f, ...patch }))
  const currency = store?.currency ?? ''
  const filtered = Boolean(filters.status || filters.from || filters.to || filters.q)

  return (
    <section className="stack wide">
      <div className="row">
        <h1>الطلبات</h1>
        <span className="spacer" />
        <button type="button" className="link" onClick={load}>تحديث</button>
      </div>

      <div className="filters">
        <input type="search" placeholder="بحث: رقم الطلب، اسم، هاتف، عنوان، منتج" aria-label="بحث بالطلبات"
          value={query} onChange={(e) => setQuery(e.target.value)} />
        <select aria-label="الحالة" value={filters.status} onChange={(e) => set({ status: e.target.value as OrderStatus | '' })}>
          <option value="">كل الحالات</option>
          {Object.entries(STATUS).map(([k, s]) => <option key={k} value={k}>{s.label}</option>)}
        </select>
        <label className="inline-label">من <input type="date" value={filters.from} onChange={(e) => set({ from: e.target.value })} /></label>
        <label className="inline-label">لـ <input type="date" value={filters.to} onChange={(e) => set({ to: e.target.value })} /></label>
        {filtered && (
          <button type="button" className="link" onClick={() => { setQuery(''); setFilters({ status: '', from: '', to: '', q: '' }) }}>
            مسح الفلاتر
          </button>
        )}
      </div>

      <IncompleteOrders />

      {error && <p className="msg error" role="alert">{error}</p>}
      {actionError && <p className="msg error" role="alert">{actionError}</p>}

      {!orders ? (
        <p className="muted">جاري التحميل...</p>
      ) : orders.length === 0 ? (
        <p className="muted">{filtered ? 'ما في طلبات بهالفلاتر.' : 'ما في طلبات لسّا. لما البوت ياخد طلب ويتأكد، بيطلع هون.'}</p>
      ) : (
        <div className="table-wrap">
          <table className="orders">
            <thead>
              <tr><th>#</th><th>العميل</th><th>المنتجات</th><th>الإجمالي ({currency})</th><th>العنوان</th><th>الحالة</th><th>التاريخ</th><th /></tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id} className={isClosedWithoutSale(o.status) ? 'inactive' : undefined}>
                  <td>
                    <strong>#{o.order_number}</strong>
                    {o.duplicate_of_number && (
                      <div><span className="badge warn" title={`العميل أكّد إنه طلب جديد، بس فيه نفس منتجات طلب #${o.duplicate_of_number} من آخر 24 ساعة`}>
                        ممكن مكرر (#{o.duplicate_of_number})
                      </span></div>
                    )}
                  </td>
                  <td>
                    {o.customer_name ?? <span className="muted">بدون اسم</span>}
                    {o.customer_phone && <div className="muted small" dir="ltr">{o.customer_phone}</div>}
                  </td>
                  <td>
                    <ul className="items">
                      {o.items.map((i, n) => (
                        <li key={n}>{itemLabel(i)} × {i.quantity}{i.notes && <span className="muted small"> ({i.notes})</span>}</li>
                      ))}
                    </ul>
                  </td>
                  <td>
                    <strong>{o.total}</strong>
                    <div className="muted small">توصيل {Number(o.delivery_fee) === 0 ? 'مجاني' : o.delivery_fee}</div>
                  </td>
                  <td className="wrap">
                    {o.zone_name && <strong>{o.zone_name}</strong>}
                    {o.delivery_address && <div className="small">{o.delivery_address}</div>}
                  </td>
                  <td><span className={`badge ${STATUS[o.status].cls}`}>{STATUS[o.status].label}</span></td>
                  <td className="small">{dateFmt.format(new Date(o.created_at))}</td>
                  <td>
                    <div className="row-actions">
                      {o.allowed_next.map((next) => (
                        <button key={next} type="button" disabled={busyId === o.id}
                          className={`link small${isClosedWithoutSale(next) ? ' danger' : ''}`}
                          onClick={() => move(o, next)}>
                          {ACTION[next]}
                        </button>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
