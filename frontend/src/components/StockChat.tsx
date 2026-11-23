import { useEffect, useRef, useState, type FormEvent } from 'react'
import { cancelOwnerAction, confirmOwnerAction, sendOwnerChat, type PendingStockAction } from '../lib/api'

type Line = { id: number; who: 'owner' | 'bot'; text: string; action?: PendingStockAction; settled?: boolean }

let nextId = 1

/**
 * Stock by chat (task 3.3). The bot only proposes; the stock changes when the owner presses
 * "تأكيد", through a separate endpoint (hard rule 7). `onChanged` refreshes the stock page.
 */
export default function StockChat({ onChanged }: { onChanged: () => void }) {
  const [lines, setLines] = useState<Line[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [lines])

  const add = (line: Omit<Line, 'id'>) => setLines((ls) => [...ls, { ...line, id: nextId++ }])
  const settle = (id: number) => setLines((ls) => ls.map((l) => (l.id === id ? { ...l, settled: true } : l)))

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const t = text.trim()
    if (!t || busy) return
    setText('')
    add({ who: 'owner', text: t })
    setBusy(true)
    try {
      const r = await sendOwnerChat(t)
      add({ who: 'bot', text: r.reply, action: r.action ?? undefined })
    } catch (err) {
      add({ who: 'bot', text: (err as Error).message })
    } finally {
      setBusy(false)
    }
  }

  const decide = async (line: Line, ok: boolean) => {
    if (!line.action || busy) return
    setBusy(true)
    try {
      if (ok) {
        const r = await confirmOwnerAction(line.action.id)
        add({ who: 'bot', text: r.reply })
        onChanged()
      } else {
        await cancelOwnerAction(line.action.id)
        add({ who: 'bot', text: 'انلغى، ما تغيّر شي.' })
      }
    } catch (err) {
      add({ who: 'bot', text: (err as Error).message })
    } finally {
      settle(line.id)   // one decision per proposal, whatever the outcome
      setBusy(false)
    }
  }

  return (
    <div className="card wide chat stock-chat">
      <h2>عدّل المخزون بالشات</h2>
      <p className="muted small">اكتب مثلاً "نقّص الفستان الأحمر M بمقدار 5" أو "قديش باقي من القميص؟". ما بيتغيّر شي قبل ما تضغط تأكيد.</p>
      <div className="chat-log" ref={logRef} aria-live="polite">
        {lines.length === 0 && <p className="muted small">لسّا ما في رسائل.</p>}
        {lines.map((l) => (
          <div key={l.id} className={`bubble ${l.who === 'owner' ? 'customer' : 'bot'}`}>
            <span className="who">{l.who === 'owner' ? 'أنت' : 'المساعد'}</span>
            {l.text}
            {l.action && !l.settled && (
              <div className="chat-buttons">
                <button type="button" disabled={busy} onClick={() => decide(l, true)}>تأكيد</button>
                <button type="button" disabled={busy} onClick={() => decide(l, false)}>إلغاء</button>
              </div>
            )}
          </div>
        ))}
      </div>
      <form className="chat-input" onSubmit={submit}>
        <input value={text} onChange={(e) => setText(e.target.value)} maxLength={500}
          placeholder="اكتب تعديل المخزون..." aria-label="رسالة لمساعد المخزون" disabled={busy} />
        <button type="submit" disabled={busy || !text.trim()}>{busy ? '...' : 'إرسال'}</button>
      </form>
    </div>
  )
}
