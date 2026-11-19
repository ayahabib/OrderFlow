import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { fetchConversationState, fetchMessages, sendMessage, startSession, type ChatMessage } from '../lib/webchat'
import { useStore } from '../store/StoreProvider'

const POLL_MS = 1500

type Saved = { token: string; name: string }

// Per-browser convenience only: remember the simulator session across reloads.
function load(key: string): Saved | null {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as Saved) : null
  } catch {
    return null
  }
}
function save(key: string, value: Saved | null) {
  try {
    if (value) localStorage.setItem(key, JSON.stringify(value))
    else localStorage.removeItem(key)
  } catch {
    /* storage blocked: the session just won't survive a reload */
  }
}

const newClientId = () => crypto.randomUUID()
const time = (iso: string) => new Date(iso).toLocaleTimeString('ar-u-nu-latn', { hour: '2-digit', minute: '2-digit' })

/** Chat as a customer of your own store, to test the bot without WhatsApp/Telegram. */
export default function SimulatorPage() {
  const { store } = useStore()
  const storageKey = `sim:${store?.id}`
  const [session, setSession] = useState<Saved | null>(() => load(storageKey))
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [text, setText] = useState('')
  const [failed, setFailed] = useState<{ text: string; clientId: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [handedOver, setHandedOver] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const begin = useCallback(async (name: string) => {
    if (!store) return
    setError(null)
    try {
      const s = await startSession(store.id)
      const saved = { token: s.session_token, name }
      save(storageKey, saved)
      setSession(saved)
      setMessages([])
      setHandedOver(false)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [store, storageKey])

  useEffect(() => {
    if (!session) begin('عميل تجريبي')
  }, [session, begin])

  const refresh = useCallback(async () => {
    if (!store || !session) return
    try {
      const [msgs, conv] = await Promise.all([
        fetchMessages(store.id, session.token),
        fetchConversationState(store.id, session.token),
      ])
      setMessages(msgs)
      setHandedOver(!conv.bot_replies)
    } catch (e) {
      // An old token (e.g. the secret changed) -> start a fresh session.
      if ((e as { status?: number }).status === 401) begin(session.name)
    }
  }, [store, session, begin])

  useEffect(() => {
    refresh()
    const timer = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(timer)
  }, [refresh])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [messages.length])

  async function deliver(body: string, clientId: string) {
    if (!store || !session) return
    setSending(true)
    setError(null)
    try {
      await sendMessage(store.id, session.token, body, clientId, session.name.trim() || null)
      setFailed(null)
      await refresh()
    } catch (e) {
      // Keep the same client id: resending is idempotent on the server.
      setFailed({ text: body, clientId })
      setError((e as Error).message)
    } finally {
      setSending(false)
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    const body = text.trim()
    if (!body) return
    setText('')
    await deliver(body, newClientId())
  }

  function rename(name: string) {
    if (!session) return
    const saved = { ...session, name }
    save(storageKey, saved)
    setSession(saved)
  }

  return (
    <section className="stack wide">
      <div className="row">
        <h1>محاكي الشات</h1>
        <span className="spacer" />
        <label className="inline-label">
          اسم العميل
          <input value={session?.name ?? ''} maxLength={80} onChange={(e) => rename(e.target.value)} />
        </label>
        <button type="button" className="link" onClick={() => begin(session?.name ?? 'عميل تجريبي')}>محادثة جديدة</button>
      </div>
      <p className="muted small">بتحكي كأنك عميل بيراسل متجرك. كل رسالة بتنحفظ متل رسائل واتساب وتيليجرام.</p>

      <div className="chat card wide">
        <div className="chat-log" aria-live="polite">
          {messages.length === 0 && <p className="muted small center">ابعت رسالة لتبلّش المحادثة.</p>}
          {messages.map((m) => (
            <div key={m.id} className={`bubble ${m.sender}`}>
              {m.sender !== 'customer' && <span className="who">{m.sender === 'owner' ? 'صاحب المتجر' : 'البوت'}</span>}
              <span className="text">{m.content}</span>
              {m.buttons.length > 0 && (
                <span className="chat-buttons">
                  {m.buttons.map((b) => (
                    <button key={b.id} type="button" className="link" disabled={sending}
                      onClick={() => deliver(b.title, newClientId())}>{b.title}</button>
                  ))}
                </span>
              )}
              <span className="time">{time(m.created_at)}</span>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        {handedOver && (
          <p className="msg warn-msg" role="status">
            هالمحادثة تحوّلت لصاحب المتجر، فالبوت ما رح يرد عليها. الرد بيكون من صفحة المحادثات (المهمة 1.12).
            {' '}<button type="button" className="link" onClick={() => begin(session?.name ?? 'عميل تجريبي')}>ابدأ محادثة جديدة</button>
          </p>
        )}

        {error && (
          <p className="msg error" role="alert">
            {error}
            {failed && <> <button type="button" className="link" onClick={() => deliver(failed.text, failed.clientId)}>إعادة الإرسال</button></>}
          </p>
        )}

        <form className="chat-input" onSubmit={submit}>
          <input aria-label="رسالة" placeholder="اكتب رسالة... مثلاً: بدي فستان احمر مقاس M" maxLength={4000}
            value={text} onChange={(e) => setText(e.target.value)} disabled={!session} />
          <button type="submit" disabled={!session || sending || !text.trim()}>إرسال</button>
        </form>
      </div>
    </section>
  )
}
