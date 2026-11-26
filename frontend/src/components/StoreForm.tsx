import { useState, type FormEvent } from 'react'
import type { StoreInput } from '../lib/api'

type Props = {
  initial: StoreInput
  submitLabel: string
  onSubmit: (input: StoreInput) => Promise<void>
}

/** Shared form for creating the store and editing it in Settings. */
export default function StoreForm({ initial, submitLabel, onSubmit }: Props) {
  const [name, setName] = useState(initial.name)
  const [phone, setPhone] = useState(initial.phone ?? '')
  const [currency, setCurrency] = useState(initial.currency)
  const [countryCode, setCountryCode] = useState(initial.phone_country_code)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      await onSubmit({
        name: name.trim(), phone: phone.trim() || null, currency: currency.trim().toUpperCase(),
        phone_country_code: countryCode.trim().replace(/^\+/, ''),
      })
      setSaved(true)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <label>
        اسم المتجر
        <input required maxLength={100} value={name}
          onChange={(e) => { setName(e.target.value); setSaved(false) }} />
      </label>
      <label>
        <span>رقم الهاتف <span className="hint">(اختياري)</span></span>
        <input type="tel" dir="ltr" maxLength={20} placeholder="0599123456" value={phone}
          onChange={(e) => { setPhone(e.target.value); setSaved(false) }} />
      </label>
      <label>
        العملة
        <input dir="ltr" required pattern="[A-Za-z]{3}" maxLength={3} title="3 حروف مثل ILS"
          value={currency} onChange={(e) => { setCurrency(e.target.value); setSaved(false) }} />
      </label>
      <label>
        <span>مفتاح الدولة <span className="hint">(لأرقام العملاء: 0599... بتصير +970599...)</span></span>
        <input dir="ltr" required pattern="\+?[1-9][0-9]{0,2}" maxLength={4} title="أرقام بس، مثل 970"
          value={countryCode} onChange={(e) => { setCountryCode(e.target.value); setSaved(false) }} />
      </label>

      {error && <p className="msg error" role="alert">{error}</p>}
      {saved && <p className="msg info">تم الحفظ ✓</p>}

      <button type="submit" disabled={busy}>{busy ? '...' : submitLabel}</button>
    </form>
  )
}
