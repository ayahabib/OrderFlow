import { useEffect, useState } from 'react'
import {
  ApiError,
  createTelegramLink,
  getTelegramStatus,
  sendTelegramTest,
  unlinkTelegram,
  verifyTelegramLink,
  type TelegramStatus,
} from '../lib/api'

/** Link the owner's Telegram so escalated conversations reach them (task 1.11). */
export default function TelegramLink() {
  const [status, setStatus] = useState<TelegramStatus | null>(null)
  const [linkUrl, setLinkUrl] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)

  useEffect(() => {
    getTelegramStatus().then(setStatus).catch((e) => setError(e instanceof ApiError ? e.message : 'صار خطأ'))
  }, [])

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setError(null)
    setInfo(null)
    try {
      await action()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'صار خطأ، حاول مرة ثانية')
    } finally {
      setBusy(false)
    }
  }

  const start = () =>
    run(async () => {
      const { url } = await createTelegramLink()
      setLinkUrl(url)
      window.open(url, '_blank', 'noopener')
    })

  const verify = () =>
    run(async () => {
      setStatus(await verifyTelegramLink())
      setLinkUrl(null)
      setInfo('تم الربط ✓ بعتنالك رسالة على Telegram.')
    })

  const test = () =>
    run(async () => {
      await sendTelegramTest()
      setInfo('انبعتت رسالة تجربة، شوفها على Telegram.')
    })

  const unlink = () =>
    run(async () => {
      if (!window.confirm('أكيد بدك توقف إشعارات Telegram؟')) return
      setStatus(await unlinkTelegram())
    })

  if (!status) return error ? <p className="msg error">{error}</p> : <p className="muted">جاري التحميل...</p>

  return (
    <div className="stack">
      {!status.available ? (
        <p className="msg warn-msg">
          إشعارات Telegram مش مفعّلة على السيرفر. لازم يتحط TELEGRAM_NOTIFY_BOT_TOKEN بملف backend/.env.
        </p>
      ) : status.linked ? (
        <>
          <p className="row">
            <span className="badge">مربوط ✓</span>
            <span className="muted small">أي محادثة بتتحوّل إلك بتوصلك على Telegram.</span>
          </p>
          <div className="row-actions">
            <button type="button" onClick={test} disabled={busy}>إرسال تجربة</button>
            <button type="button" className="link small danger" onClick={unlink} disabled={busy}>فك الربط</button>
          </div>
        </>
      ) : (
        <>
          <p className="muted small">
            اضغط "ربط Telegram"، رح ينفتح البوت، اضغط فيه Start، وبعدين ارجع لهون واضغط "تحقق".
          </p>
          <div className="row-actions">
            <button type="button" onClick={start} disabled={busy}>ربط Telegram</button>
            {linkUrl && (
              <button type="button" onClick={verify} disabled={busy}>تحقق</button>
            )}
          </div>
          {linkUrl && (
            <p className="muted small">
              إذا ما انفتح البوت: <a href={linkUrl} target="_blank" rel="noopener noreferrer">افتح الرابط</a> (صالح لمدة ساعة).
            </p>
          )}
        </>
      )}
      {error && <p className="msg error">{error}</p>}
      {info && <p className="msg info">{info}</p>}
    </div>
  )
}
