import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listIncompleteOrders, type StalledDraft } from '../lib/api'

const REFRESH_MS = 60_000
const timeFmt = new Intl.DateTimeFormat('ar-u-nu-latn', { weekday: 'short', hour: 'numeric', minute: '2-digit' })

/**
 * Orders a customer started and left (task 3.5): the draft has items but was never confirmed,
 * and the customer has been quiet for over 2 hours. Hidden when there are none.
 */
export default function IncompleteOrders() {
  const [drafts, setDrafts] = useState<StalledDraft[]>([])
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setDrafts(await listIncompleteOrders())
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    load()
    const timer = window.setInterval(load, REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [load])

  if (error) return <p className="msg error">{error}</p>
  if (drafts.length === 0) return null

  return (
    <details className="card wide incomplete-orders">
      <summary>
        <strong>طلبات ما كملت ({drafts.length})</strong>
        <span className="muted small"> عملاء بلّشوا طلب ووقفوا عن الرد أكتر من ساعتين</span>
      </summary>
      <div className="table-wrap">
        <table>
          <thead><tr><th>العميل</th><th>المنتجات</th><th>ناقص</th><th>آخر رسالة</th><th /></tr></thead>
          <tbody>
            {drafts.map((d) => (
              <tr key={d.conversation_id}>
                <td>
                  {d.customer_name ?? <span className="muted">بدون اسم</span>}
                  {d.customer_phone && <div className="muted small" dir="ltr">{d.customer_phone}</div>}
                </td>
                <td>{d.items.map((i) => <div key={i}>{i}</div>)}</td>
                <td>{d.missing.join('، ')}</td>
                <td className="small">{timeFmt.format(new Date(d.last_message_at))}</td>
                <td><Link to={`/conversations/${d.conversation_id}`}>فتح المحادثة</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}
