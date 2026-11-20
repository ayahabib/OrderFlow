import { type FormEvent, useEffect, useState } from 'react'
import {
  ApiError,
  connectTelegramChannel,
  disconnectTelegramChannel,
  getTelegramChannel,
  type TelegramChannelStatus,
} from '../lib/api'

/** Connect the store's own Telegram bot so customers can order through it (task 1.13). */
export default function TelegramChannel() {
  const [status, setStatus] = useState<TelegramChannelStatus | null>(null)
  const [token, setToken] = useState('')
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getTelegramChannel().then(setStatus).catch((e) => setError(e instanceof ApiError ? e.message : 'صار خطأ'))
  }, [])

  async function connect(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setStatus(await connectTelegramChannel(token.trim()))
      setToken('')  // never keep the token around in the page
      setEditing(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'صار خطأ، حاول مرة ثانية')
    } finally {
      setBusy(false)
    }
  }

  async function disconnect() {
    if (!window.confirm('أكيد بدك تفصل بوت Telegram؟ العملاء ما رح يوصلهم رد عليه.')) return
    setBusy(true)
    setError(null)
    try {
      setStatus(await disconnectTelegramChannel())
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
            <span className={`badge ${status.webhook_ok === false ? 'danger' : ''}`}>
              {status.webhook_ok === false ? 'في مشكلة' : 'مربوط ✓'}
            </span>
            {status.bot_username && (
              <a href={`https://t.me/${status.bot_username}`} target="_blank" rel="noopener noreferrer">
                @{status.bot_username}
              </a>
            )}
          </p>
          {status.webhook_error && <p className="msg warn-msg">{status.webhook_error}</p>}
          {!editing && (
            <div className="row-actions">
              <button type="button" onClick={() => setEditing(true)} disabled={busy}>إعادة الربط</button>
              <button type="button" className="link small danger" onClick={disconnect} disabled={busy}>فصل البوت</button>
            </div>
          )}
        </>
      )}

      {showForm && (
        <form className="stack" onSubmit={connect}>
          <p className="muted small">
            اعمل بوت للمتجر من @BotFather (أمر /newbot)، وانسخ التوكن هون. لازم يكون بوت غير بوت الإشعارات.
          </p>
          <label>
            توكن البوت
            <input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)}
              placeholder="123456789:AA..." dir="ltr" />
          </label>
          <div className="row-actions">
            <button type="submit" disabled={busy || !token.trim()}>{busy ? '...' : 'ربط البوت'}</button>
            {editing && <button type="button" className="link" onClick={() => setEditing(false)}>إلغاء</button>}
          </div>
        </form>
      )}
      {error && <p className="msg error">{error}</p>}
    </div>
  )
}
