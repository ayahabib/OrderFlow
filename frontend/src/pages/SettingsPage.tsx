import StoreForm from '../components/StoreForm'
import TelegramLink from '../components/TelegramLink'
import ZonesEditor from '../components/ZonesEditor'
import { updateStore } from '../lib/api'
import { useStore } from '../store/StoreProvider'

export default function SettingsPage() {
  const { store, setStore } = useStore()
  if (!store) return null

  return (
    <section className="stack wide">
      <h1>الإعدادات</h1>

      <h2>معلومات المتجر</h2>
      <div className="narrow-block">
        <StoreForm
          key={store.id}
          initial={{ name: store.name, phone: store.phone, currency: store.currency }}
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
