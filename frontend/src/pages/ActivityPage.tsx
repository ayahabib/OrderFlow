import { useCallback, useEffect, useState } from 'react'
import { listAudit, type AuditEntry } from '../lib/api'

const ACTOR: Record<AuditEntry['actor'], string> = { owner: 'أنت', bot: 'البوت', system: 'النظام' }

// What each recorded action means, in the owner's words.
const ACTION: Record<string, string> = {
  'order.create': 'سجّل طلب جديد',
  'conversation.escalate': 'حوّل محادثة إلك',
  'interest.create': 'سجّل عميل مستني منتج',
  update_status: 'غيّر حالة طلب',
  create_product: 'أضاف منتج', add_variant: 'أضاف لون/مقاس', update_product: 'عدّل سعر منتج',
  update_variant: 'عدّل نسخة منتج', delete_product: 'حذف منتج', delete_variant: 'حذف لون/مقاس',
  confirm_import: 'رفع ملف مخزون',
  create_offer: 'أضاف عرض', update_offer: 'عدّل عرض', delete_offer: 'حذف عرض',
  create_zone: 'أضاف منطقة توصيل', update_zone: 'عدّل منطقة توصيل', delete_zone: 'حذف منطقة توصيل',
  add_entry: 'أضاف معلومة للبوت', upload_file: 'رفع ملف معلومات', edit_entry: 'عدّل معلومة', delete_entry: 'حذف معلومة',
  pause_bot: 'أوقف البوت على محادثة', resume_bot: 'رجّع البوت على محادثة', owner_reply: 'رد على عميل',
  answer_escalation: 'جاوب سؤال محوّل',
  connect_telegram: 'ربط بوت Telegram', disconnect_telegram: 'فصل بوت Telegram',
  verify_link: 'ربط تنبيهات Telegram', unlink: 'فصل تنبيهات Telegram',
  confirm: 'أكّد تعديل مخزون بالشات', cancel: 'لغى تعديل مخزون بالشات',
  notify_waiting: 'بلّغ عملاء إنه منتج رجع',
  update_my_store: 'عدّل إعدادات المتجر',
}

const FIELD: Record<string, string> = {
  status: 'الحالة', stock_quantity: 'الكمية', price_override: 'السعر', low_stock_threshold: 'حد التنبيه',
  price: 'السعر', name: 'الاسم', phone: 'الهاتف', currency: 'العملة', phone_country_code: 'مفتاح الدولة',
  order_number: 'رقم الطلب', total: 'الإجمالي', rule: 'السبب', fee: 'الأجرة', product: 'المنتج',
}
const STATUS: Record<string, string> = {
  new: 'جديد', in_progress: 'قيد التجهيز', shipped: 'تم الشحن', delivered: 'تم التوصيل', cancelled: 'ملغي', returned: 'مرتجع',
}

const timeFmt = new Intl.DateTimeFormat('ar-u-nu-latn', { dateStyle: 'medium', timeStyle: 'short' })
const show = (k: string, v: unknown) => (v === null || v === undefined ? '—' : k === 'status' ? STATUS[String(v)] ?? String(v) : String(v))

function Details({ e }: { e: AuditEntry }) {
  const keys = Object.keys({ ...(e.before ?? {}), ...(e.after ?? {}) }).filter((k) => FIELD[k])
  if (keys.length === 0) return null
  return (
    <span className="small">
      {keys.map((k) => (
        <span key={k} className="audit-change">
          {FIELD[k]}: {e.before && k in e.before ? <>{show(k, e.before[k])} ← </> : null}<strong>{show(k, e.after?.[k])}</strong>
        </span>
      ))}
    </span>
  )
}

/** Audit log (task 3.14): who changed what and when, newest first. Read only. */
export default function ActivityPage() {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null)
  const [actor, setActor] = useState('')
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setEntries(await listAudit(actor))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [actor])

  useEffect(() => { load() }, [load])

  return (
    <section className="stack wide">
      <div className="row">
        <h1>سجل النشاط</h1>
        <span className="spacer" />
        <button type="button" className="link" onClick={load}>تحديث</button>
      </div>
      <p className="muted small">كل تغيير بالمتجر، منك أو من البوت، بيتسجّل هون ومش ممكن ينمسح. آخر 100 تغيير.</p>
      <div className="filters">
        <select aria-label="مين" value={actor} onChange={(e) => setActor(e.target.value)}>
          <option value="">الكل</option>
          <option value="owner">أنت</option>
          <option value="bot">البوت</option>
        </select>
      </div>
      {error && <p className="msg error" role="alert">{error}</p>}
      {!entries ? <p className="muted">جاري التحميل...</p> : entries.length === 0 ? (
        <p className="muted">ما في نشاط مسجّل لسّا.</p>
      ) : (
        <div className="table-wrap">
          <table className="orders">
            <thead><tr><th>الوقت</th><th>مين</th><th>شو صار</th><th>التفاصيل</th></tr></thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id}>
                  <td className="small">{timeFmt.format(new Date(e.created_at))}</td>
                  <td><span className={`badge ${e.actor === 'bot' ? 'muted-badge' : ''}`}>{ACTOR[e.actor]}</span></td>
                  <td>{ACTION[e.action] ?? e.action}</td>
                  <td><Details e={e} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
