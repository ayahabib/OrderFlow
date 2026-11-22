import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  getConversation,
  listConversations,
  pauseConversation,
  replyToConversation,
  resumeConversation,
  type ConversationDetail,
  type ConversationMode,
  type ConversationSummary,
  type ConversationView,
} from '../lib/api'

const MODE: Record<ConversationMode, { label: string; cls: string }> = {
  waiting_owner: { label: 'بانتظار ردك', cls: 'danger' },
  owner: { label: 'إنت عم ترد', cls: 'warn' },
  paused: { label: 'البوت موقوف', cls: 'warn' },
  bot: { label: 'البوت شغّال', cls: '' },
  closed: { label: 'مسكّرة', cls: 'muted-badge' },
}

const VIEWS: { value: ConversationView; label: string }[] = [
  { value: '', label: 'الكل' },
  { value: 'waiting', label: 'بانتظار ردك' },
  { value: 'owner', label: 'إنت عم ترد' },
  { value: 'bot', label: 'البوت شغّال' },
  { value: 'closed', label: 'مسكّرة' },
]

// Labels match the backend's escalation rules (services/escalation.py).
const RULE: Record<string, string> = {
  complaint: 'شكوى',
  discount: 'طلب خصم أو سعر خاص',
  large_order: 'طلبية كبيرة',
  allergy_ingredients: 'حساسية أو مكونات',
  unknown_zone: 'ما في مناطق توصيل مسجلة',
  human_requested: 'طلب يحكي مع شخص',
  not_in_data: 'سؤال مش موجود ببيانات المتجر',
  order_status: 'سؤال عن حالة طلب',
  return_request: 'طلب إرجاع أو تبديل',
  llm_failure: 'خطأ تقني بفهم الرسالة',
}

const WAITING_FOR: Record<string, string> = {
  variant: 'اللون/المقاس', product: 'أي منتج', stock: 'كمية أقل', address: 'العنوان', zone: 'المنطقة', phone: 'رقم التلفون',
}

const LIST_MS = 10_000
const DETAIL_MS = 4_000
const timeFmt = new Intl.DateTimeFormat('ar-u-nu-latn', { dateStyle: 'short', timeStyle: 'short' })
const fmt = (iso: string) => timeFmt.format(new Date(iso))

function customerLabel(c: { customer_name: string | null; customer_phone: string | null }) {
  return c.customer_name || c.customer_phone || 'عميل بدون اسم'
}

export default function ConversationsPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [list, setList] = useState<ConversationSummary[] | null>(null)
  const [view, setView] = useState<ConversationView>('')
  const [query, setQuery] = useState('')
  const [q, setQ] = useState('')
  const [error, setError] = useState<string | null>(null)

  const loadList = useCallback(async () => {
    try {
      setList(await listConversations(view, q))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [view, q])

  useEffect(() => {
    loadList()
    const timer = window.setInterval(loadList, LIST_MS)
    return () => window.clearInterval(timer)
  }, [loadList])

  useEffect(() => {
    const t = window.setTimeout(() => setQ(query.trim()), 300)
    return () => window.clearTimeout(t)
  }, [query])

  return (
    <section className="stack wide conv-page">
      <div className="conversations">
        <div className={`conv-list ${id ? 'has-selection' : ''}`}>
          <div className="conv-list-head">
            <div className="row">
              <h1>المحادثات</h1>
              <span className="spacer" />
              <button type="button" className="link small" onClick={loadList}>تحديث</button>
            </div>
            <div className="tabs" role="tablist" aria-label="فلترة حسب الحالة">
              {VIEWS.map((v) => (
                <button key={v.value} type="button" role="tab" aria-selected={view === v.value}
                  className={view === v.value ? 'active' : ''} onClick={() => setView(v.value)}>{v.label}</button>
              ))}
            </div>
            <input type="search" placeholder="بحث بالاسم، الرقم أو نص الرسالة" value={query}
              onChange={(e) => setQuery(e.target.value)} />
          </div>
          {error && <p className="msg error">{error}</p>}
          {!list ? <p className="muted conv-empty-list">جاري التحميل...</p> : list.length === 0 ? (
            <p className="muted small conv-empty-list">{view || q ? 'ما في محادثات بهالفلتر.' : 'ما في محادثات لسّا. جرّب من محاكي الشات.'}</p>
          ) : (
            <ul className="conv-items">
              {list.map((c) => (
                <li key={c.id}>
                  <button type="button" className={`conv-item ${c.id === id ? 'active' : ''} ${c.mode === 'waiting_owner' ? 'waiting' : ''}`}
                    onClick={() => navigate(`/conversations/${c.id}`)}>
                    <span className="conv-item-top">
                      <strong>{customerLabel(c)}</strong>
                      <span className="muted small conv-time">{fmt(c.last_message_at)}</span>
                    </span>
                    <span className="muted small conv-preview">
                      {c.last_sender === 'bot' ? 'البوت: ' : c.last_sender === 'owner' ? 'إنت: ' : ''}{c.last_message ?? ''}
                    </span>
                    {c.mode !== 'bot' && (  // the bot answering is the normal case: no chip
                      <span className={`badge ${MODE[c.mode].cls}`}>{MODE[c.mode].label}</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="conv-detail">
          {id ? <ConversationPanel key={id} id={id} onChanged={loadList} /> : (
            <div className="conv-empty">
              <p>اختار محادثة من القائمة</p>
              <p className="muted small">المحادثات الي بانتظار ردك معلّمة بالأصفر.</p>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

function ConversationPanel({ id, onChanged }: { id: string; onChanged: () => void }) {
  const [conv, setConv] = useState<ConversationDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    try {
      setConv(await getConversation(id))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [id])

  useEffect(() => {
    load()
    const timer = window.setInterval(load, DETAIL_MS)  // the customer's new messages show up
    return () => window.clearInterval(timer)
  }, [load])

  const count = conv?.messages.length ?? 0
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [count])

  async function act(action: () => Promise<ConversationDetail>) {
    setBusy(true)
    setError(null)
    try {
      setConv(await action())
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function send(e: FormEvent) {
    e.preventDefault()
    const body = text.trim()
    if (!body) return
    await act(() => replyToConversation(id, body))
    setText('')
  }

  if (!conv) return error ? <p className="msg error">{error}</p> : <p className="muted">جاري التحميل...</p>

  const botAnswering = conv.mode === 'bot'
  const open = conv.escalations.filter((e) => e.status === 'open')

  return (
    <div className="conv-panel">
      <div className="row conv-head">
        <Link to="/conversations" className="small back-link">→ القائمة</Link>
        <div className="conv-who">
          <h2 className="no-margin">{customerLabel(conv)}</h2>
          <span className="muted small">
            {conv.channel === 'web' ? 'محاكي الويب' : conv.channel === 'telegram' ? 'Telegram' : conv.channel}
            {conv.customer_phone && conv.customer_name ? `، ${conv.customer_phone}` : ''}
          </span>
        </div>
        <span className={`badge ${MODE[conv.mode].cls}`}>{MODE[conv.mode].label}</span>
        <span className="spacer" />
        {botAnswering && (
          <button type="button" onClick={() => act(() => pauseConversation(id))} disabled={busy}>أوقف البوت</button>
        )}
        {!botAnswering && conv.mode !== 'closed' && !conv.draft && (
          <button type="button" onClick={() => act(() => resumeConversation(id, false))} disabled={busy}>رجّع البوت</button>
        )}
      </div>

      {open.length > 0 && (
        <div className="msg warn-msg">
          <strong>تحويل مفتوح: </strong>
          {open.map((e) => `${RULE[e.rule ?? ''] ?? 'تحويل'} («${e.question}»)`).join('، ')}
        </div>
      )}

      {conv.draft && (
        <div className="card wide">
          <strong>طلب مفتوح كان البوت عم يجمعه</strong>
          {conv.draft.items.length > 0 ? (
            <ul className="draft-items">
              {conv.draft.items.map((i, n) => (
                <li key={n}>{i.quantity} × {i.product_name}{i.variant_name !== 'الافتراضي' ? ` (${i.variant_name})` : ''}</li>
              ))}
            </ul>
          ) : <p className="muted small">ما في منتجات محددة لسّا.</p>}
          {conv.draft.unresolved > 0 && <p className="muted small">{conv.draft.unresolved} منتج لسا مش محدد.</p>}
          <p className="muted small">
            العنوان: {conv.draft.address ?? '—'}{conv.draft.zone_name ? ` (${conv.draft.zone_name})` : ''} · التلفون: {conv.draft.phone ?? '—'}
            {conv.draft.waiting_for && ` · البوت كان ناطر: ${WAITING_FOR[conv.draft.waiting_for] ?? conv.draft.waiting_for}`}
          </p>
          {!botAnswering && conv.mode !== 'closed' && (
            <div className="row-actions">
              <button type="button" onClick={() => act(() => resumeConversation(id, true))} disabled={busy}>
                رجّع البوت وكمّل نفس الطلب
              </button>
              <button type="button" className="link" onClick={() => act(() => resumeConversation(id, false))} disabled={busy}>
                رجّع البوت وابدأ من جديد
              </button>
            </div>
          )}
        </div>
      )}

      <div className="chat card wide">
        <div className="chat-log" aria-live="polite">
          {conv.messages.map((m) => (
            <div key={m.id} className={`bubble ${m.sender}`}>
              {m.sender !== 'customer' && <span className="who">{m.sender === 'owner' ? 'إنت' : 'البوت'}</span>}
              <span className="text">{m.content}</span>
              <span className="time">{fmt(m.created_at)}</span>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
        {error && <p className="msg error">{error}</p>}
        {conv.can_reply ? (
          <form className="chat-input" onSubmit={send}>
            <input value={text} onChange={(e) => setText(e.target.value)} maxLength={2000}
              placeholder={botAnswering ? 'اكتب ردك (البوت رح يوقف على هالمحادثة)' : 'اكتب ردك للعميل'} />
            <button type="submit" disabled={busy || !text.trim()}>إرسال</button>
          </form>
        ) : (
          <p className="muted small center">
            {conv.mode === 'closed' ? 'المحادثة مسكّرة.' : 'الرد من الداشبورد على هالقناة بيشتغل بعد ربطها.'}
          </p>
        )}
      </div>
    </div>
  )
}
