import { type FormEvent, useEffect, useState } from 'react'
import {
  ApiError, cancelWhatsAppRequest, createWhatsAppRequest, getWhatsAppRequest, type WhatsAppRequest as Request,
} from '../lib/api'

const dateFmt = new Intl.DateTimeFormat('ar-u-nu-latn', { day: 'numeric', month: 'long' })

/** Step-by-step guide and request form: the platform admin connects the number (task 5.4). */
export default function WhatsAppRequest({ onConnected }: { onConnected: () => void }) {
  const [request, setRequest] = useState<Request | null | undefined>(undefined)
  const [phone, setPhone] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getWhatsAppRequest()
      .then((r) => {
        setRequest(r)
        if (r?.status === 'connected') onConnected()
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : 'صار خطأ'))
  }, [onConnected])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setRequest(await createWhatsAppRequest({ phone, display_name: displayName.trim(), confirm_not_on_app: confirmed }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'صار خطأ، حاول مرة ثانية')
    } finally {
      setBusy(false)
    }
  }

  async function cancel() {
    if (!window.confirm('تلغي طلب ربط واتساب؟')) return
    setBusy(true)
    try {
      await cancelWhatsAppRequest()
      setRequest(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'صار خطأ، حاول مرة ثانية')
    } finally {
      setBusy(false)
    }
  }

  if (request === undefined) return error ? <p className="msg error">{error}</p> : <p className="muted">جاري التحميل...</p>

  if (request?.status === 'pending') {
    return (
      <div className="stack">
        <p className="msg warn-msg">
          طلبك للرقم <span dir="ltr">{request.phone}</span> وصل للإدارة بتاريخ {dateFmt.format(new Date(request.created_at))}.
          رح نتواصل معك لما نحتاج كود التحقق اللي بيوصل على الرقم.
        </p>
        <ol className="small steps">
          <li className="done">قدّمت الطلب.</li>
          <li>الإدارة بتضيف الرقم على حساب واتساب للأعمال تبع المنصة.</li>
          <li>بيوصلك كود تحقق برسالة SMS أو اتصال على الرقم: ابعته للإدارة لما يطلبوه.</li>
          <li>بعد الربط بتظهر هون كلمة "مربوط"، وجرّب تبعت رسالة للرقم من موبايل تاني.</li>
        </ol>
        <div className="row-actions">
          <button type="button" className="link small danger" onClick={cancel} disabled={busy}>إلغاء الطلب</button>
        </div>
        {error && <p className="msg error" role="alert">{error}</p>}
      </div>
    )
  }

  return (
    <div className="stack">
      {request?.status === 'rejected' && (
        <p className="msg error">الإدارة رفضت الطلب السابق: {request.reject_reason}. بتقدر تصلّح وتقدّم من جديد.</p>
      )}
      <details className="guide" open={!request}>
        <summary>كيف بيصير الربط؟ (دليل كامل)</summary>
        <ol className="small steps">
          <li>
            <strong>جهّز رقم خاص بالمتجر.</strong> رقم موبايل أو أرضي بيستقبل SMS أو اتصال، ومش رقمك الشخصي:
            البوت رح يرد على كل رسالة بتوصل عليه.
          </li>
          <li>
            <strong>الرقم لازم ما يكون مفعّل على تطبيق واتساب أو واتساب بزنس.</strong> إذا كان مفعّل، احذف الحساب من
            التطبيق أول (الإعدادات ← الحساب ← حذف الحساب). بعد الربط ما بتقدر تستخدمه من التطبيق، والرسائل بتظهر هون بالمحادثات.
          </li>
          <li>
            <strong>اختار الاسم اللي بيظهر للزبائن.</strong> لازم يكون قريب من اسم متجرك الحقيقي (Meta بتراجعه)،
            بدون كلمات عامة متل "عروض" أو "واتساب".
          </li>
          <li><strong>عبّي الطلب تحت.</strong> الإدارة بتشوفه وبتضيف الرقم على حساب المنصة.</li>
          <li><strong>كود التحقق:</strong> بيوصلك على الرقم، وبتبعته للإدارة لما يطلبوه.</li>
          <li><strong>جرّب:</strong> بعد ما يصير "مربوط"، ابعت رسالة للرقم من موبايل تاني، ولازم يرد البوت.</li>
        </ol>
      </details>

      <form className="stack" onSubmit={submit}>
        <label>
          رقم واتساب للمتجر
          <input dir="ltr" type="tel" required minLength={6} maxLength={25} placeholder="0599 123 456"
            value={phone} onChange={(e) => setPhone(e.target.value)} />
        </label>
        <label>
          الاسم اللي بيظهر للزبائن
          <input required minLength={2} maxLength={100} value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </label>
        <label className="inline-check">
          <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} required />
          بأكد إن الرقم مش مستخدم على تطبيق واتساب أو واتساب بزنس
        </label>
        {error && <p className="msg error" role="alert">{error}</p>}
        <div className="row-actions">
          <button type="submit" disabled={busy}>{busy ? '...' : 'قدّم طلب الربط'}</button>
        </div>
      </form>
    </div>
  )
}
