import { useCallback, useEffect, useState } from 'react'
import { listAdminStores, setStoreDemo, setStoreSuspended, type AdminStore } from '../lib/api'

const dateFmt = new Intl.DateTimeFormat('ar-u-nu-latn', { day: 'numeric', month: 'short', year: 'numeric' })
const CHANNELS: Record<string, string> = { whatsapp: 'واتساب', telegram: 'تيليجرام', web: 'ويب' }

/** Every store on the platform (task 5.2): status, channels, activity; suspend and demo flag. */
export default function AdminStoresPage() {
  const [stores, setStores] = useState<AdminStore[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setStores(await listAdminStores())
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function run(id: string, action: () => Promise<unknown>) {
    setBusy(id)
    try {
      await action()
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  function toggleSuspend(s: AdminStore) {
    const suspend = !s.suspended_at
    if (suspend && !window.confirm(`توقف متجر "${s.name}"؟ البوت تبعه ما رح يرد على حدا لحد ما ترجعي تفعّليه.`)) return
    run(s.id, () => setStoreSuspended(s.id, suspend))
  }

  return (
    <section className="stack wide">
      <h1>المتاجر</h1>
      <p className="muted small">
        المتجر التجريبي ما بينحسب بأرقام المنصة. الموقوف بتنحفظ رسائله بس البوت ما بيرد، وصاحبه بيشوف إنه موقوف.
      </p>
      {error && <p className="msg error" role="alert">{error}</p>}
      {!stores && !error && <p className="muted">جاري التحميل...</p>}
      {stores && stores.length === 0 && <p className="muted">ما في متاجر لسّا.</p>}
      {stores && stores.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>المتجر</th><th>تاريخ التسجيل</th><th>القنوات</th><th>آخر نشاط</th>
                <th>الطلبات</th><th>الحالة</th><th>تجريبي</th><th></th>
              </tr>
            </thead>
            <tbody>
              {stores.map((s) => (
                <tr key={s.id}>
                  <td>
                    <strong>{s.name}</strong>
                    {s.owner_email && <div className="muted small" dir="ltr">{s.owner_email}</div>}
                  </td>
                  <td>{dateFmt.format(new Date(s.created_at))}</td>
                  <td>{s.channels.length ? s.channels.map((c) => CHANNELS[c] ?? c).join('، ') : <span className="muted">—</span>}</td>
                  <td>{s.last_activity ? dateFmt.format(new Date(s.last_activity)) : <span className="muted">—</span>}</td>
                  <td>{s.orders_count}</td>
                  <td>
                    {s.suspended_at
                      ? <span className="badge danger">موقوف</span>
                      : s.bot_enabled ? <span className="badge">فعّال</span> : <span className="badge warn">البوت مطفي</span>}
                  </td>
                  <td>
                    <input type="checkbox" checked={s.is_demo} disabled={busy === s.id}
                      aria-label={`متجر تجريبي: ${s.name}`} onChange={(e) => run(s.id, () => setStoreDemo(s.id, e.target.checked))} />
                  </td>
                  <td>
                    <button type="button" className={s.suspended_at ? 'secondary' : 'link danger'} disabled={busy === s.id}
                      onClick={() => toggleSuspend(s)}>
                      {s.suspended_at ? 'تفعيل' : 'إيقاف'}
                    </button>
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
