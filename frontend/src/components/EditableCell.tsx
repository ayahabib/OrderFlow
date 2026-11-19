import { useState, type KeyboardEvent } from 'react'

type Props = {
  value: string
  /** Return an error message for invalid input, or null if it is fine. */
  validate: (text: string) => string | null
  onSave: (text: string) => Promise<void>
  placeholder?: string
  label: string
  width?: string
  /** Text fields (e.g. a zone name) pass dir="auto" and inputMode="text". */
  dir?: 'ltr' | 'rtl' | 'auto'
  inputMode?: 'decimal' | 'numeric' | 'text'
}

/** A number input that saves on Enter or blur, with saving / saved / error state. */
export default function EditableCell({
  value, validate, onSave, placeholder, label, width = '6rem', dir = 'ltr', inputMode = 'decimal',
}: Props) {
  const [text, setText] = useState(value)
  const [lastSaved, setLastSaved] = useState(value)
  const [state, setState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)

  // The parent may change the value (e.g. after a reload); follow it unless the user is mid-edit.
  if (value !== lastSaved && text === lastSaved) {
    setText(value)
    setLastSaved(value)
  }

  async function commit() {
    const trimmed = text.trim()
    if (trimmed === lastSaved) return
    const problem = validate(trimmed)
    if (problem) {
      setState('error')
      setError(problem)
      return
    }
    setState('saving')
    setError(null)
    try {
      await onSave(trimmed)
      setLastSaved(trimmed)
      setState('saved')
    } catch (err) {
      setState('error')
      setError((err as Error).message)
    }
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') e.currentTarget.blur()
    if (e.key === 'Escape') {
      setText(lastSaved)
      setState('idle')
      setError(null)
    }
  }

  return (
    <span className="editable">
      <input
        dir={dir}
        inputMode={inputMode}
        aria-label={label}
        aria-invalid={state === 'error'}
        className={state === 'error' ? 'invalid' : undefined}
        style={{ width }}
        value={text}
        placeholder={placeholder}
        disabled={state === 'saving'}
        onChange={(e) => { setText(e.target.value); if (state !== 'saving') setState('idle') }}
        onBlur={commit}
        onKeyDown={onKey}
      />
      {state === 'saved' && <span className="ok" aria-label="تم الحفظ">✓</span>}
      {state === 'error' && <span className="error-text small" role="alert">{error}</span>}
    </span>
  )
}

export function validateQuantity(text: string): string | null {
  return /^\d+$/.test(text) && Number(text) <= 1_000_000 ? null : 'رقم صحيح من 0 وفوق'
}

/** Empty is allowed only when `allowEmpty` (clearing a variant's special price). */
export function validatePrice(text: string, allowEmpty = false): string | null {
  if (text === '') return allowEmpty ? null : 'السعر مطلوب'
  return /^\d{1,8}(\.\d{1,2})?$/.test(text) ? null : 'سعر غير صالح (مثل 120 أو 99.50)'
}
