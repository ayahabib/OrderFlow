import { type FormEvent, useEffect, useState } from 'react'
import {
  addKnowledge,
  deleteKnowledge,
  listKnowledge,
  searchKnowledge,
  updateKnowledge,
  uploadKnowledge,
  type KnowledgeEntry,
  type KnowledgeHit,
} from '../lib/api'

const SOURCE: Record<KnowledgeEntry['source'], string> = {
  manual: 'كتابة يدوية',
  upload: 'من ملف',
  owner_answer: 'من جوابك لعميل',
}

/** Store info the bot will answer customers from (task 2.1; answering itself is 2.2). */
export default function KnowledgePage() {
  const [entries, setEntries] = useState<KnowledgeEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')

  useEffect(() => {
    listKnowledge().then(setEntries).catch((e) => setError((e as Error).message))
  }, [])

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setError(null)
    setInfo(null)
    try {
      await action()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const add = (e: FormEvent) => {
    e.preventDefault()
    return run(async () => {
      const created = await addKnowledge({ question: question.trim() || null, answer: answer.trim() })
      setEntries((list) => [created, ...(list ?? [])])
      setQuestion('')
      setAnswer('')
    })
  }

  const upload = (file: File) =>
    run(async () => {
      const created = await uploadKnowledge(file)
      setEntries((list) => [...created, ...(list ?? [])])
      setInfo(`انضافت ${created.length} معلومة من الملف.`)
    })

  const replace = (updated: KnowledgeEntry) =>
    setEntries((list) => list?.map((x) => (x.id === updated.id ? updated : x)) ?? null)

  const remove = (entry: KnowledgeEntry) =>
    run(async () => {
      if (!window.confirm('حذف هالمعلومة؟ البوت ما رح يقدر يجاوب منها بعدها.')) return
      await deleteKnowledge(entry.id)
      setEntries((list) => list?.filter((x) => x.id !== entry.id) ?? null)
    })

  return (
    <section className="stack wide">
      <h1>معلومات المتجر</h1>
      <p className="muted small">
        هون بتكتب المعلومات الي البوت بيجاوب منها: أوقات الدوام، سياسة التوصيل، الإرجاع والتبديل... البوت ما بيخترع
        إشي مش مكتوب هون. الأسعار والتوفر والمناطق بيجيبها البوت من صفحات المنتجات والإعدادات، مش من هون.
      </p>

      <div className="card wide">
        <form className="stack wide" onSubmit={add}>
          <label>
            السؤال <span className="hint">(اختياري، مثلاً: شو أوقات الدوام؟)</span>
            <input value={question} maxLength={300} onChange={(e) => setQuestion(e.target.value)} />
          </label>
          <label>
            المعلومة
            <textarea rows={3} value={answer} maxLength={2000} onChange={(e) => setAnswer(e.target.value)}
              placeholder="مثلاً: منفتح من 10 الصبح لـ 8 المسا، كل يوم ما عدا الجمعة." />
          </label>
          <div className="row-actions">
            <button type="submit" disabled={busy || !answer.trim()}>{busy ? '...' : 'إضافة'}</button>
            <label className="link-button">
              أو ارفع ملف نص (.txt)
              <input type="file" accept=".txt,.md,text/plain" hidden disabled={busy}
                onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) upload(f) }} />
            </label>
          </div>
          <p className="muted small">بالملف: كل فقرة (بينها وبين الي بعدها سطر فاضي) بتصير معلومة لحالها.</p>
        </form>
        {error && <p className="msg error">{error}</p>}
        {info && <p className="msg info">{info}</p>}
      </div>

      <SearchBox />

      <h2>المعلومات المحفوظة</h2>
      {!entries ? <p className="muted">جاري التحميل...</p> : entries.length === 0 ? (
        <p className="muted small">ما في معلومات لسّا.</p>
      ) : (
        <ul className="kb-list">
          {entries.map((e) => (
            <EntryRow key={e.id} entry={e} onChanged={replace} onDelete={() => remove(e)} onError={setError} />
          ))}
        </ul>
      )}
    </section>
  )
}

function EntryRow({ entry, onChanged, onDelete, onError }: {
  entry: KnowledgeEntry
  onChanged: (e: KnowledgeEntry) => void
  onDelete: () => void
  onError: (m: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [question, setQuestion] = useState(entry.question ?? '')
  const [answer, setAnswer] = useState(entry.answer)
  const [busy, setBusy] = useState(false)

  async function save(changes: Parameters<typeof updateKnowledge>[1]) {
    setBusy(true)
    try {
      onChanged(await updateKnowledge(entry.id, changes))
      setEditing(false)
    } catch (e) {
      onError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className={`card wide kb-item ${entry.is_active ? '' : 'inactive'}`}>
      {editing ? (
        <div className="stack wide">
          <input value={question} maxLength={300} placeholder="السؤال (اختياري)" onChange={(e) => setQuestion(e.target.value)} />
          <textarea rows={3} value={answer} maxLength={2000} onChange={(e) => setAnswer(e.target.value)} />
          <div className="row-actions">
            <button type="button" disabled={busy || !answer.trim()}
              onClick={() => save({ question: question.trim(), answer: answer.trim() })}>حفظ</button>
            <button type="button" className="link" onClick={() => setEditing(false)}>إلغاء</button>
          </div>
        </div>
      ) : (
        <>
          {entry.question && <strong>{entry.question}</strong>}
          <p className="kb-answer">{entry.answer}</p>
          <div className="row">
            <span className="badge muted-badge">{SOURCE[entry.source]}</span>
            {!entry.is_active && <span className="badge warn">موقوفة</span>}
            <span className="spacer" />
            <button type="button" className="link small" onClick={() => setEditing(true)} disabled={busy}>تعديل</button>
            <button type="button" className="link small" onClick={() => save({ is_active: !entry.is_active })} disabled={busy}>
              {entry.is_active ? 'إيقاف' : 'تشغيل'}
            </button>
            <button type="button" className="link small danger" onClick={onDelete} disabled={busy}>حذف</button>
          </div>
        </>
      )}
    </li>
  )
}

/** Lets the owner check that a differently worded question finds the right info. */
function SearchBox() {
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<KnowledgeHit[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function search(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setHits(await searchKnowledge(query.trim()))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card wide">
      <form className="chat-input kb-search" onSubmit={search}>
        <input value={query} maxLength={500} onChange={(e) => setQuery(e.target.value)}
          placeholder="جرّب سؤال متل ما بيكتبه العميل، مثلاً: لإيمتى فاتحين اليوم؟" />
        <button type="submit" disabled={busy || !query.trim()}>{busy ? '...' : 'جرّب البحث'}</button>
      </form>
      {error && <p className="msg error">{error}</p>}
      {hits && (hits.length === 0 ? <p className="muted small">ما في معلومات مفعّلة للبحث فيها.</p> : (
        <ol className="kb-hits">
          {hits.map((h) => (
            <li key={h.id}>
              <span className="badge muted-badge">تطابق {Math.round(h.similarity * 100)}%</span>{' '}
              {h.question && <strong>{h.question} </strong>}
              <span>{h.answer}</span>
            </li>
          ))}
        </ol>
      ))}
    </div>
  )
}
