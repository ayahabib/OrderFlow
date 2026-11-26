import { useEffect, useState, type FormEvent } from 'react'
import EditableCell, { validatePrice } from './EditableCell'
import { createZone, deleteZone, listZones, updateZone, type Zone } from '../lib/api'

const byName = (a: Zone, b: Zone) => a.name.localeCompare(b.name, 'ar')
const validateName = (t: string) => (t.trim() ? null : 'اسم المنطقة مطلوب')

/**
 * Delivery zones and fees, used in the sign-up step and in Settings.
 * The bot only quotes fees from this list; unknown or paused areas go to the owner.
 */
export default function ZonesEditor({ currency, onChange }: { currency: string; onChange?: (zones: Zone[]) => void }) {
  const [zones, setZonesState] = useState<Zone[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [fee, setFee] = useState('')
  const [busy, setBusy] = useState(false)

  function setZones(update: (list: Zone[]) => Zone[]) {
    setZonesState((list) => {
      const next = update(list ?? []).sort(byName)
      onChange?.(next)
      return next
    })
  }

  useEffect(() => {
    // Load once on mount.
    listZones().then((z) => setZones(() => z)).catch((e) => setError((e as Error).message))
  }, [])

  const replace = (z: Zone) => setZones((list) => list.map((x) => (x.id === z.id ? z : x)))

  async function add(e: FormEvent) {
    e.preventDefault()
    setActionError(null)
    const problem = validateName(name) ?? validatePrice(fee.trim())
    if (problem) return setActionError(problem)
    setBusy(true)
    try {
      const zone = await createZone({ name: name.trim(), fee: fee.trim() })
      setZones((list) => [...list, zone])
      setName('')
      setFee('')
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // Pickup from the store (3.15): one zone with fee 0; the bot offers it when asking for the address.
  async function addPickup() {
    setActionError(null)
    setBusy(true)
    try {
      const zone = await createZone({ name: 'استلام من المحل', fee: '0', is_pickup: true })
      setZones((list) => [...list, zone])
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function toggle(z: Zone) {
    setActionError(null)
    try {
      replace(await updateZone(z.id, { is_active: !z.is_active }))
    } catch (err) {
      setActionError((err as Error).message)
    }
  }

  async function remove(z: Zone) {
    if (!window.confirm(`حذف منطقة "${z.name}"؟`)) return
    setActionError(null)
    try {
      await deleteZone(z.id)
      setZones((list) => list.filter((x) => x.id !== z.id))
    } catch (err) {
      setActionError((err as Error).message)
    }
  }

  if (error) return <p className="msg error" role="alert">{error}</p>
  if (!zones) return <p className="muted">جاري التحميل...</p>

  return (
    <div className="stack wide">
      <form className="variant-row" onSubmit={add}>
        <input aria-label="اسم المنطقة" placeholder="اسم المنطقة (مثلاً نابلس)" maxLength={100}
          value={name} onChange={(e) => setName(e.target.value)} />
        <input aria-label="أجرة التوصيل" placeholder={`الأجرة (${currency})`} dir="ltr" inputMode="decimal"
          className="narrow" value={fee} onChange={(e) => setFee(e.target.value)} />
        <button type="submit" disabled={busy}>{busy ? '...' : '+ إضافة'}</button>
      </form>
      {!zones.some((z) => z.is_pickup) && (
        <p className="small">
          <button type="button" className="link small" disabled={busy} onClick={addPickup}>+ خيار استلام من المحل (بدون أجرة)</button>
        </p>
      )}

      {actionError && <p className="msg error" role="alert">{actionError}</p>}

      {zones.length === 0 ? (
        <p className="muted small">ما في مناطق لسّا.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>المنطقة</th><th>الأجرة ({currency})</th><th>الحالة</th><th /></tr>
            </thead>
            <tbody>
              {zones.map((z) => (
                <tr key={z.id} className={z.is_active ? undefined : 'inactive'}>
                  <td>
                    <EditableCell label={`اسم ${z.name}`} value={z.name} width="11rem" dir="auto" inputMode="text"
                      validate={validateName} onSave={async (t) => replace(await updateZone(z.id, { name: t }))} />
                  </td>
                  <td>
                    {z.is_pickup ? <span className="muted small">بدون أجرة (استلام)</span> : (
                      <EditableCell label={`أجرة ${z.name}`} value={z.fee} width="5rem" validate={(t) => validatePrice(t)}
                        onSave={async (t) => replace(await updateZone(z.id, { fee: t }))} />
                    )}
                  </td>
                  <td>
                    {z.is_active ? <span className="badge">فعّالة</span> : <span className="badge muted-badge">موقوفة</span>}
                  </td>
                  <td>
                    <div className="row-actions">
                      <button type="button" className="link small" onClick={() => toggle(z)}>{z.is_active ? 'إيقاف' : 'تشغيل'}</button>
                      <button type="button" className="link small danger" onClick={() => remove(z)}>حذف</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
