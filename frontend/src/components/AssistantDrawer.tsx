import { useEffect, useRef, useState, type FormEvent } from 'react'
import { askAssistant } from '../lib/api'

type Line = { id: number; who: 'owner' | 'bot'; text: string }
let nextId = 1

const EXAMPLES = ['قديش بعنا هالشهر؟', 'شو أكثر منتج انباع؟', 'كم طلب جديد عنا؟', 'شو اللي قرّب يخلص؟']

/**
 * Read-only assistant on every dashboard page (task 3.13): questions about sales, orders and
 * stock. Answers come from the database; it never changes anything.
 */
export default function AssistantDrawer() {
  const [open, setOpen] = useState(false)
  const [lines, setLines] = useState<Line[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { logRef.current?.scrollTo({ top: logRef.current.scrollHeight }) }, [lines])
  useEffect(() => { if (open) inputRef.current?.focus() }, [open])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const add = (who: Line['who'], t: string) => setLines((ls) => [...ls, { id: nextId++, who, text: t }])

  async function ask(question: string) {
    const q = question.trim()
    if (!q || busy) return
    setText('')
    add('owner', q)
    setBusy(true)
    try {
      add('bot', (await askAssistant(q)).reply)
    } catch (err) {
      add('bot', (err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const submit = (e: FormEvent) => { e.preventDefault(); ask(text) }

  return (
    <>
      <button type="button" className="assistant-fab" aria-expanded={open} aria-controls="assistant-drawer"
        onClick={() => setOpen((o) => !o)}>
        {open ? 'إغلاق' : 'اسأل عن متجرك'}
      </button>
      {open && (
        <aside className="assistant-drawer card chat" id="assistant-drawer" aria-label="مساعد المتجر">
          <div className="row">
            <h2>مساعد المتجر</h2>
            <span className="spacer" />
            <button type="button" className="link" onClick={() => setOpen(false)}>إغلاق</button>
          </div>
          <p className="muted small">بيجاوب من أرقام متجرك بس، وما بيغيّر إشي.</p>
          <div className="chat-log" ref={logRef} aria-live="polite">
            {lines.length === 0 && (
              <div className="assistant-examples">
                {EXAMPLES.map((e) => (
                  <button key={e} type="button" className="link small" disabled={busy} onClick={() => ask(e)}>{e}</button>
                ))}
              </div>
            )}
            {lines.map((l) => (
              <div key={l.id} className={`bubble ${l.who === 'owner' ? 'customer' : 'bot'}`}>
                <span className="who">{l.who === 'owner' ? 'أنت' : 'المساعد'}</span>
                {l.text}
              </div>
            ))}
            {busy && <p className="muted small">عم بدوّر بالأرقام...</p>}
          </div>
          <form className="chat-input" onSubmit={submit}>
            <input ref={inputRef} value={text} onChange={(e) => setText(e.target.value)} maxLength={500}
              placeholder="اسأل عن المبيعات، الطلبات أو المخزون..." aria-label="سؤال للمساعد" disabled={busy} />
            <button type="submit" disabled={busy || !text.trim()}>{busy ? '...' : 'اسأل'}</button>
          </form>
        </aside>
      )}
    </>
  )
}
