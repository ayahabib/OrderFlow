import { useCallback, useEffect, useState } from 'react'
import {
  connectWhatsAppRequest, listAdminWhatsAppRequests, rejectWhatsAppRequest, type AdminWhatsAppRequest,
} from '../lib/api'

const dateFmt = new Intl.DateTimeFormat('ar-u-nu-latn', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })
const STATUS: Record<string, string> = { pending: 'بانتظارك', connected: 'مربوط', rejected: 'مرفوض' }

/** One open request: add the number in Meta, then paste its Phone Number ID here. */
function RequestCard({ req, onDone }: { req: AdminWhatsAppRequest; onDone: () => void }) {
  const [phoneNumberId, setPhoneNumberId] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run(action: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await action()
      onDone()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card wide stack">
      <div>
        <h2>{req.store_name}</h2>
        <p className="muted small">
          الرقم <span dir="ltr">{req.phone}</span> · الاسم الظاهر للزبائن: {req.display_name} · {dateFmt.format(new Date(req.created_at))}
        </p>
      </div>
      <ol className="small steps">
        <li>من Meta Business Manager ← WhatsApp Manager ← Phone numbers ← Add phone number: أضيفي الرقم على حساب المنصة بنفس الاسم الظاهر.</li>
        <li>صاحب المتجر بيوصله كود تحقق (SMS أو اتصال)، خديه منه وكمّلي التحقق.</li>
        <li>انسخي الـ Phone Number ID للرقم الجديد من API Setup وحطيه هون. النظام بيجرّب الاتصال قبل ما يحفظ.</li>
      </ol>
      <form className="inline-form" onSubmit={(e) => { e.preventDefault(); run(() => connectWhatsAppRequest(req.id, phoneNumberId.trim())) }}>
        <label>
          Phone Number ID
          <input dir="ltr" inputMode="numeric" required pattern="\d{6,25}" value={phoneNumberId}
            onChange={(e) => setPhoneNumberId(e.target.value)} />
        </label>
        <button type="submit" disabled={busy}>اربط بالمتجر</button>
      </form>
      <form className="inline-form" onSubmit={(e) => { e.preventDefault(); run(() => rejectWhatsAppRequest(req.id, reason.trim())) }}>
        <label>
          سبب الرفض (بيشوفه صاحب المتجر)
          <input required minLength={2} maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        <button type="submit" className="secondary" disabled={busy}>ارفض</button>
      </form>
      {error && <p className="msg error" role="alert">{error}</p>}
    </div>
  )
}

/** WhatsApp connection requests from stores (task 5.4). */
export default function AdminWhatsAppPage() {
  const [items, setItems] = useState<AdminWhatsAppRequest[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setItems(await listAdminWhatsAppRequests(''))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const open = (items ?? []).filter((r) => r.status === 'pending')
  const done = (items ?? []).filter((r) => r.status !== 'pending')

  return (
    <section className="stack wide">
      <h1>طلبات ربط واتساب</h1>
      <p className="muted small">
        الأرقام بتنضاف على حساب واتساب للأعمال (WABA) تبع المنصة، وبتبعت بتوكن المنصة الدائم (WHATSAPP_SYSTEM_USER_TOKEN).
      </p>
      {error && <p className="msg error" role="alert">{error}</p>}
      {!items && !error && <p className="muted">جاري التحميل...</p>}
      {items && open.length === 0 && <p className="msg info">ما في طلبات مستنية.</p>}
      {open.map((r) => <RequestCard key={r.id} req={r} onDone={load} />)}

      {done.length > 0 && (
        <>
          <h2>طلبات سابقة</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>المتجر</th><th>الرقم</th><th>الحالة</th><th>التاريخ</th><th>ملاحظة</th></tr></thead>
              <tbody>
                {done.map((r) => (
                  <tr key={r.id}>
                    <td>{r.store_name}</td>
                    <td dir="ltr">{r.phone}</td>
                    <td>{STATUS[r.status]}</td>
                    <td>{r.handled_at ? dateFmt.format(new Date(r.handled_at)) : '—'}</td>
                    <td>{r.status === 'connected' ? <span dir="ltr">{r.phone_number_id}</span> : r.reject_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}
