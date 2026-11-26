import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { getCustomer, listCustomers, type CustomerDetail, type CustomerSummary } from '../lib/api'
import { useStore } from '../store/StoreProvider'
import { isClosedWithoutSale, ORDER_STATUS } from '../lib/orderStatus'

const CHANNEL: Record<string, string> = { telegram: 'Telegram', whatsapp: 'WhatsApp', web: 'المحاكي' }
const CONV_STATUS: Record<string, string> = { bot: 'البوت', waiting_owner: 'بانتظار ردك', owner: 'أنت', closed: 'مسكّرة' }

const dateFmt = new Intl.DateTimeFormat('ar-u-nu-latn', { dateStyle: 'medium' })
const dateTimeFmt = new Intl.DateTimeFormat('ar-u-nu-latn', { dateStyle: 'medium', timeStyle: 'short' })
const fmtDate = (iso: string | null) => (iso ? dateFmt.format(new Date(iso)) : '—')

function lastActive(c: CustomerSummary): string | null {
  const times = [c.last_order_at, c.last_message_at].filter(Boolean) as string[]
  return times.length ? times.sort().at(-1)! : null
}

function Channels({ list }: { list: string[] }) {
  return <>{list.map((ch) => <span key={ch} className="badge muted-badge">{CHANNEL[ch] ?? ch}</span>)}</>
}

/** Customers (task 3.9): one row per person; the same phone on two channels is one customer. */
export default function CustomersPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { store } = useStore()
  const currency = store?.currency ?? ''
  const [customers, setCustomers] = useState<CustomerSummary[] | null>(null)
  const [query, setQuery] = useState('')
  const [q, setQ] = useState('')  // applied after a short pause
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setCustomers(await listCustomers(q))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [q])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    const t = window.setTimeout(() => setQ(query.trim()), 300)
    return () => window.clearTimeout(t)
  }, [query])

  return (
    <section className="stack wide">
      <div className="row">
        <h1>العملاء</h1>
        <span className="spacer" />
        <button type="button" className="link" onClick={load}>تحديث</button>
      </div>
      <p className="muted small">
        العميل الي راسلك من أكثر من قناة بنفس رقم الهاتف بيطلع هون مرة وحدة. المجموع بيحسب كل الطلبات إلا الملغية والمرتجعة.
      </p>

      {id && <CustomerCard id={id} currency={currency} onClose={() => navigate('/customers')} />}

      <div className="filters">
        <input type="search" placeholder="بحث: اسم أو رقم هاتف" aria-label="بحث بالعملاء"
          value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>

      {error && <p className="msg error" role="alert">{error}</p>}

      {!customers ? (
        <p className="muted">جاري التحميل...</p>
      ) : customers.length === 0 ? (
        <p className="muted">{q ? 'ما في عملاء بهالبحث.' : 'ما في عملاء لسّا. أول ما حدا يراسل المتجر بيطلع هون.'}</p>
      ) : (
        <div className="table-wrap">
          <table className="orders">
            <thead>
              <tr><th>العميل</th><th>القنوات</th><th>الطلبات</th><th>المجموع ({currency})</th><th>آخر طلب</th><th>آخر نشاط</th></tr>
            </thead>
            <tbody>
              {customers.map((c) => (
                <tr key={c.id} className={`clickable${c.id === id ? ' selected' : ''}`}
                  onClick={() => navigate(`/customers/${c.id}`)}>
                  <td>
                    <Link to={`/customers/${c.id}`} onClick={(e) => e.stopPropagation()}>
                      {c.name ?? <span className="muted">بدون اسم</span>}
                    </Link>
                    {c.phone && <div className="muted small" dir="ltr">{c.phone}</div>}
                  </td>
                  <td><Channels list={c.channels} /></td>
                  <td>{c.orders_count}</td>
                  <td><strong>{c.total_spent}</strong></td>
                  <td className="small">{fmtDate(c.last_order_at)}</td>
                  <td className="small">{fmtDate(lastActive(c))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function CustomerCard({ id, currency, onClose }: { id: string; currency: string; onClose: () => void }) {
  const [c, setC] = useState<CustomerDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const top = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let live = true
    // The row clicked may be far down the list: bring the card into view.
    top.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    setC(null)
    setError(null)
    getCustomer(id).then((d) => live && setC(d)).catch((e) => live && setError((e as Error).message))
    return () => { live = false }
  }, [id])

  if (error) return <p ref={top} className="msg error" role="alert">{error}</p>
  if (!c) return <div ref={top} className="card customer-card"><p className="muted">جاري التحميل...</p></div>

  return (
    <div ref={top} className="card customer-card">
      <div className="row">
        <h2>{c.name ?? 'بدون اسم'}</h2>
        <Channels list={c.channels} />
        <span className="spacer" />
        <button type="button" className="link" onClick={onClose}>إغلاق</button>
      </div>

      <div className="stats">
        <div className="stat"><span className="value">{c.orders_count}</span><span className="label">طلب</span></div>
        <div className="stat"><span className="value">{c.total_spent}</span><span className="label">المجموع ({currency})</span></div>
        <div className="stat"><span className="value small-value">{fmtDate(c.first_seen_at)}</span><span className="label">أول تواصل</span></div>
      </div>

      <dl className="facts">
        <dt>الهاتف</dt><dd dir="ltr">{c.phone ?? '—'}</dd>
        <dt>العنوان</dt><dd>{c.address ?? '—'}</dd>
      </dl>

      <h3>الطلبات</h3>
      {c.orders.length === 0 ? <p className="muted small">ما في طلبات.</p> : (
        <ul className="plain-list">
          {c.orders.map((o) => (
            <li key={o.id} className={isClosedWithoutSale(o.status) ? 'inactive' : undefined}>
              <strong>#{o.order_number}</strong>
              <span className={`badge ${ORDER_STATUS[o.status].cls}`}>{ORDER_STATUS[o.status].label}</span>
              <span>{o.total} {currency}</span>
              <span className="muted small">{o.items_count} منتج · {dateTimeFmt.format(new Date(o.created_at))}</span>
            </li>
          ))}
        </ul>
      )}

      <h3>المحادثات</h3>
      {c.conversations.length === 0 ? <p className="muted small">ما في محادثات.</p> : (
        <ul className="plain-list">
          {c.conversations.map((v) => (
            <li key={v.id}>
              <Link to={`/conversations/${v.id}`}>{CHANNEL[v.channel] ?? v.channel}</Link>
              <span className="badge muted-badge">{CONV_STATUS[v.status] ?? v.status}</span>
              <span className="muted small">آخر رسالة {dateTimeFmt.format(new Date(v.last_message_at))}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
