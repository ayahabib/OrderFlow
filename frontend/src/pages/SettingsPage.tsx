import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import StoreForm from '../components/StoreForm'
import TelegramChannel from '../components/TelegramChannel'
import WhatsAppChannel from '../components/WhatsAppChannel'
import TelegramLink from '../components/TelegramLink'
import ZonesEditor from '../components/ZonesEditor'
import { updateStore } from '../lib/api'
import { useStore } from '../store/StoreProvider'

export default function SettingsPage() {
  const { store, setStore } = useStore()
  const { hash } = useLocation()

  // Links like /settings#zones (from a handed-over conversation) open at that section.
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView({ block: 'start' })
  }, [hash, store])

  if (!store) return null

  return (
    <section className="stack wide">
      <h1>الإعدادات</h1>

      <h2>معلومات المتجر</h2>
      <div className="narrow-block">
        <StoreForm
          key={store.id}
          initial={{ name: store.name, phone: store.phone, currency: store.currency, phone_country_code: store.phone_country_code }}
          submitLabel="حفظ"
          onSubmit={async (input) => setStore(await updateStore(input))}
        />
      </div>

      <h2 id="zones">مناطق التوصيل</h2>
      <p className="muted small">
        البوت بيحسب أجرة التوصيل من هالقائمة بس. إذا العميل من منطقة مش موجودة أو موقوفة، البوت بيعتذر وبيذكرله المناطق الي بتوصلوا عليها. وإذا القائمة فاضية، الطلب بيتحوّل إلك.
      </p>
      <div className="card wide">
        <ZonesEditor currency={store.currency} />
      </div>

      <h2 id="channel-whatsapp">قناة واتساب للعملاء</h2>
      <p className="muted small">
        عن طريق WhatsApp Cloud API الرسمي من Meta. العملاء بيراسلوا رقم المتجر، والبوت بيرد متل المحاكي بالظبط.
      </p>
      <div className="card wide narrow-block">
        <WhatsAppChannel />
      </div>

      <h2 id="channel-telegram">قناة Telegram للعملاء</h2>
      <p className="muted small">
        العملاء بيراسلوا بوت متجرك على Telegram، والبوت بيستقبل طلباتهم متل المحاكي بالظبط.
      </p>
      <div className="card wide narrow-block">
        <TelegramChannel />
      </div>

      <h2 id="telegram">إشعارات Telegram</h2>
      <p className="muted small">
        لما البوت يحوّل محادثة إلك (شكوى، طلب خصم، سؤال ما بيعرف جوابه...) بتوصلك رسالة فيها السبب وآخر الرسائل.
      </p>
      <div className="card wide narrow-block">
        <TelegramLink />
      </div>
    </section>
  )
}
