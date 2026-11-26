import { type FormEvent, useEffect, useState } from 'react'
import {
  ApiError,
  connectWhatsAppChannel,
  disconnectWhatsAppChannel,
  getWhatsAppChannel,
  type WhatsAppChannelStatus,
} from '../lib/api'

/** Connect the store's WhatsApp number over the official Cloud API (task 5.1). */
export default function WhatsAppChannel() {
  const [status, setStatus] = useState<WhatsAppChannelStatus | null>(null)
  const [phoneNumberId, setPhoneNumberId] = useState('')
  const [token, setToken] = useState('')
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getWhatsAppChannel().then(setStatus).catch((e) => setError(e instanceof ApiError ? e.message : 'صار خطأ'))
  }, [])

  async function connect(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setStatus(await connectWhatsAppChannel(phoneNumberId.trim(), token.trim()))
      setToken('')  // never keep the token around in the page
      setEditing(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'صار خطأ، حاول مرة ثانية')
    } finally {
      setBusy(false)
    }
  }

  async function disconnect() {
    if (!window.confirm('أكيد بدك تفصل رقم واتساب؟ العملاء ما رح يوصلهم رد عليه.')) return
    setBusy(true)
    setError(null)
    try {
      setStatus(await disconnectWhatsAppChannel())
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'صار خطأ، حاول مرة ثانية')
    } finally {
      setBusy(false)
    }
  }

  if (!status) return error ? <p className="msg error">{error}</p> : <p className="muted">جاري التحميل...</p>

  const showForm = !status.connected || editing

  return (
    <div className="stack">
      {status.connected && (
        <>
          <p className="row">
            <span className="badge">مربوط ✓</span>
            <span dir="ltr">{status.display_phone_number}</span>
            {status.verified_name && <span className="muted small">({status.verified_name})</span>}
          </p>
          {!editing && (
            <div className="row-actions">
              <button type="button" onClick={() => setEditing(true)} disabled={busy}>إعادة الربط</button>
              <button type="button" className="link small danger" onClick={disconnect} disabled={busy}>فصل الرقم</button>
            </div>
          )}
        </>
      )}

      {!status.webhook_ready && (
        <p className="msg warn-msg">
          السيرفر لسا مش جاهز يستقبل رسائل واتساب: بده رابط https (ngrok أو النشر) بـ PUBLIC_BASE_URL،
          و WHATSAPP_APP_SECRET و WHATSAPP_VERIFY_TOKEN بملف .env.
        </p>
      )}
      <p className="muted small">
        رابط الـ webhook (بينحط مرة وحدة بتطبيق Meta ← WhatsApp ← Configuration):{' '}
        <code dir="ltr">{status.webhook_url}</code>
      </p>

      {showForm && (
        <form className="stack" onSubmit={connect}>
          <p className="muted small">
            من تطبيقك على developers.facebook.com ← WhatsApp ← API Setup: انسخ الـ Phone Number ID والـ Access Token.
            استعمل رقم خاص بالمتجر، مش رقمك الشخصي: البوت بيرد على كل رسالة بتوصل للرقم.
          </p>
          <label>
            Phone Number ID
            <input dir="ltr" required inputMode="numeric" pattern="\d{6,25}" value={phoneNumberId}
              onChange={(e) => setPhoneNumberId(e.target.value)} />
          </label>
          <label>
            Access Token
            <input type="password" dir="ltr" required minLength={20} autoComplete="off" value={token}
              onChange={(e) => setToken(e.target.value)} />
          </label>
          {error && <p className="msg error" role="alert">{error}</p>}
          <div className="row-actions">
            <button type="submit" disabled={busy}>{busy ? '...' : 'ربط الرقم'}</button>
            {editing && <button type="button" className="link small" onClick={() => setEditing(false)}>إلغاء</button>}
          </div>
        </form>
      )}
      {!showForm && error && <p className="msg error" role="alert">{error}</p>}
    </div>
  )
}
