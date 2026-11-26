import { useState, type FormEvent } from 'react'
import { supabase } from '../lib/supabase'

type Mode = 'login' | 'register'

/** Map common Supabase auth errors to Arabic. */
function authError(message: string): string {
  if (/invalid login credentials/i.test(message)) return 'الإيميل أو كلمة السر غلط'
  if (/email not confirmed/i.test(message)) return 'لازم تأكد إيميلك أول (افتح رابط التأكيد)'
  if (/already registered/i.test(message)) return 'هالإيميل مسجّل من قبل، سجّل دخول'
  if (/password should be at least/i.test(message)) return 'كلمة السر لازم تكون 6 أحرف على الأقل'
  if (/rate limit/i.test(message)) return 'محاولات كتير، استنى شوي وجرّب'
  return message
}

export default function LoginPage() {
  const [mode, setMode] = useState<Mode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)

  // Password reset (3.15): Supabase emails a link back to /reset-password, where the new password is set.
  async function forgot() {
    setError(null)
    setInfo(null)
    if (!email.trim()) return setError('اكتب إيميلك فوق أول، وبعدين اكبس "نسيت كلمة السر؟"')
    setBusy(true)
    try {
      const { error } = await supabase.auth.resetPasswordForEmail(email.trim(), {
        redirectTo: `${window.location.origin}/reset-password`,
      })
      if (error) throw error
      setInfo('إذا الإيميل مسجّل عنا، بيوصلك رابط لتغيير كلمة السر. افتحه من نفس الجهاز.')
    } catch (err) {
      setError(authError((err as Error).message))
    } finally {
      setBusy(false)
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setInfo(null)
    try {
      if (mode === 'login') {
        const { error } = await supabase.auth.signInWithPassword({ email, password })
        if (error) throw error
      } else {
        const { data, error } = await supabase.auth.signUp({ email, password })
        if (error) throw error
        // With "Confirm email" on, Supabase returns no session until the link is clicked.
        if (!data.session) setInfo('بعتنالك رابط تأكيد على الإيميل. افتحه وبعدين سجّل دخول.')
      }
    } catch (err) {
      setError(authError((err as Error).message))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-side" aria-hidden="true">
        <div className="chat-sample">
          <span className="c">بدي فستانين أحمر M، التوصيل ع نابلس</span>
          <span className="b">تمام! المجموع 250 شيكل مع التوصيل. بأكّد الطلب؟</span>
          <span className="c">في خصم إذا أخدت 3؟</span>
          <span className="n">حوّلت السؤال إلك</span>
        </div>
        <h2>متجرك بيرد على الزباين وإنت مش فاضي</h2>
        <p>البوت بياخد الطلبات من الشات، بيحسب المجموع من أسعارك، وبيحوّل إلك أي إشي ما بيعرفه.</p>
      </section>
      <div className="auth-main">
      <form className="card" onSubmit={submit}>
        <h1>{mode === 'login' ? 'تسجيل الدخول' : 'حساب جديد'}</h1>

        <label>
          الإيميل
          <input type="email" dir="ltr" required autoComplete="email"
            value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          كلمة السر
          <input type="password" dir="ltr" required minLength={6}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>

        {error && <p className="msg error" role="alert">{error}</p>}
        {info && <p className="msg info">{info}</p>}

        <button type="submit" disabled={busy}>
          {busy ? '...' : mode === 'login' ? 'دخول' : 'إنشاء الحساب'}
        </button>

        <button type="button" className="link"
          onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(null); setInfo(null) }}>
          {mode === 'login' ? 'ما عندك حساب؟ سجّل هون' : 'عندك حساب؟ سجّل دخول'}
        </button>
        {mode === 'login' && (
          <button type="button" className="link small" disabled={busy} onClick={forgot}>نسيت كلمة السر؟</button>
        )}
      </form>
      </div>
    </main>
  )
}
