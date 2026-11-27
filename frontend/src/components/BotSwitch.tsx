import { useState } from 'react'
import { updateStore } from '../lib/api'
import { useStore } from '../store/StoreProvider'

/** Turn the store-wide auto-reply on or off (task 5.5). Shared by the sidebar, the banner and Settings. */
function useBotSwitch() {
  const { store, setStore } = useStore()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function setEnabled(enabled: boolean) {
    if (!enabled && !window.confirm('توقف الرد الآلي على كل المحادثات؟ الرسائل رح توصلك والرد بيكون منك.')) return
    setBusy(true)
    setError(null)
    try {
      setStore(await updateStore({ bot_enabled: enabled }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return { enabled: store?.bot_enabled ?? true, busy, error, setEnabled }
}

/** On/off switch with its state in words, so it reads without colour. */
export function BotSwitch({ className = '' }: { className?: string }) {
  const { enabled, busy, error, setEnabled } = useBotSwitch()
  return (
    <div className={`bot-switch ${enabled ? 'on' : 'off'} ${className}`}>
      <label>
        <input type="checkbox" role="switch" checked={enabled} disabled={busy}
          onChange={(e) => setEnabled(e.target.checked)} />
        <span>الرد الآلي: <strong>{enabled ? 'شغّال' : 'موقوف'}</strong></span>
      </label>
      {error && <p className="error-text" role="alert">{error}</p>}
    </div>
  )
}

/** Shown above every page while the auto-reply is off, so the owner doesn't forget. */
export function BotOffBanner() {
  const { enabled, busy, error, setEnabled } = useBotSwitch()
  if (enabled) return null
  return (
    <div className="bot-off-banner" role="status">
      <span>
        <strong>الرد الآلي موقوف.</strong> البوت مش راح يرد على حدا، والرسائل بتوصلك على المحادثات.
      </span>
      <button type="button" onClick={() => setEnabled(true)} disabled={busy}>شغّل الرد الآلي</button>
      {error && <p className="error-text" role="alert">{error}</p>}
    </div>
  )
}
