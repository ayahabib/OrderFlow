# ARCHITECTURE.md: معمارية النظام

> النسخة 0.1 (مسودة). بتعتمد على `PRD.md` و `DATABASE.md`.

## 1. الصورة الكبيرة

```
   العميل                                    صاحب المتجر
(WhatsApp / Telegram / Web chat)         (متصفح: React Dashboard)
        │                                          │
        ▼                                          ▼
┌─────────────────┐                      ┌──────────────────┐
│ Channel Adapters │                      │  React + Vite     │
│  - whatsapp      │                      │  (Supabase Auth)  │
│  - telegram      │                      └────────┬─────────┘
│  - web           │                               │ REST (JWT)
└────────┬─────────┘                               │
         │ InboundMessage (صيغة موحدة)             │
         ▼                                          ▼
┌───────────────────────────────────────────────────────────┐
│                    FastAPI Backend                         │
│  ┌──────────────┐  ┌───────────┐  ┌────────────────────┐  │
│  │ Conversation  │  │ Services  │  │   LLM Service       │  │
│  │ Engine        │─►│ orders    │  │ (OpenAI wrapper)    │  │
│  │ (state machine│  │ inventory │◄─│ extraction / RAG /  │  │
│  │  + rules)     │  │ knowledge │  │ tool-calling        │  │
│  └──────────────┘  │ escalation│  └────────────────────┘  │
│                    │ notify    │                           │
│                    └───────────┘                           │
└──────────────────────────┬────────────────────────────────┘
                           ▼
              ┌──────────────────────────┐
              │ Supabase                  │
              │  PostgreSQL + pgvector    │
              │  Auth + Storage           │
              └──────────────────────────┘
```

**الفكرة الأساسية:** الـ LLM بيفهم الكلام، والكود هو الي بيقرر وبينفذ. الـ LLM ما بيلمس الداتا بيز مباشرة ولا بيحسب أسعار.

## 2. هيكلة المشروع (Monorepo)

```
project/
├── CLAUDE.md
├── docs/                    # PRD, DATABASE, ARCHITECTURE, TASKS
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py                # قراءة .env
│   │   ├── db/                      # اتصال Postgres مباشر (asyncpg) + queries بـ SQL، كلها بتفلتر بـ store_id
│   │   ├── api/                     # routes لصاحب المتجر (JWT)
│   │   │   ├── products.py
│   │   │   ├── orders.py
│   │   │   ├── conversations.py
│   │   │   ├── escalations.py
│   │   │   ├── knowledge.py
│   │   │   ├── reports.py
│   │   │   └── owner_chat.py
│   │   ├── webhooks/                # routes القنوات (بدون JWT، بتتحقق بسر القناة)
│   │   │   ├── telegram.py
│   │   │   ├── whatsapp.py
│   │   │   └── web_chat.py
│   │   ├── channels/                # Channel Adapters
│   │   │   ├── base.py              # InboundMessage / OutboundMessage / ChannelAdapter
│   │   │   ├── telegram.py
│   │   │   ├── whatsapp.py
│   │   │   └── web.py
│   │   ├── engine/                  # محرك المحادثة
│   │   │   ├── handler.py           # نقطة الدخول: handle_inbound()
│   │   │   ├── rules.py             # قواعد التحويل الإجبارية
│   │   │   ├── order_flow.py        # state machine للطلب
│   │   │   └── qa.py                # الإجابة على الأسئلة (RAG)
│   │   ├── llm/                     # كل التعامل مع OpenAI هون فقط
│   │   │   ├── client.py            # retries, timeouts, logging
│   │   │   ├── schemas.py           # Pydantic schemas للـ Structured Output
│   │   │   ├── extraction.py
│   │   │   ├── embeddings.py
│   │   │   └── owner_tools.py       # Function Calling لشات صاحب المتجر
│   │   ├── services/                # business logic (بدون LLM)
│   │   │   ├── orders.py
│   │   │   ├── inventory.py
│   │   │   ├── product_matching.py
│   │   │   ├── excel_import.py
│   │   │   ├── knowledge.py
│   │   │   ├── escalation.py
│   │   │   ├── notifications.py
│   │   │   └── forecasting.py
│   │   └── utils/arabic.py          # توحيد النص العربي
│   ├── tests/
│   └── pyproject.toml               # uv (Python 3.12)
├── frontend/
│   └── src/ (pages: Login, Orders, Inventory, Dashboard, Conversations, Escalations, Settings, WebChatSimulator)
├── eval/
│   ├── extraction_testset.jsonl     # رسائل عربية بلهجات + الناتج المتوقع
│   └── run_eval.py
└── supabase/
    └── migrations/                  # SQL من DATABASE.md
```

## 3. الـ Channel Adapter

كل قناة بتحول رسالتها لصيغة موحدة، وباقي النظام ما بيعرف من أي قناة إجت الرسالة.

```python
# channels/base.py (شكل تقريبي)
class InboundMessage(BaseModel):
    store_id: UUID
    channel: Literal["whatsapp", "telegram", "web"]
    external_customer_id: str     # رقم واتساب أو chat_id
    customer_name: str | None
    text: str
    external_message_id: str | None
    raw: dict                     # الرسالة الأصلية للتصحيح

class ChannelAdapter(Protocol):
    def parse_webhook(self, request) -> InboundMessage | None: ...
    async def send_text(self, to: str, text: str) -> None: ...
    async def send_buttons(self, to: str, text: str, buttons: list[Button]) -> None: ...
```

### الـ Webhooks

| القناة | الـ endpoint | كيف نعرف المتجر | التحقق |
|---|---|---|---|
| Telegram | `POST /webhooks/telegram/{channel_id}` | `channel_id` من `store_channels` | Secret token بالـ header |
| WhatsApp | `GET/POST /webhooks/whatsapp` | `phone_number_id` من الـ payload ← `store_channels.external_id` | verify token (للـ GET) وتوقيع الطلب (للـ POST) |
| Web chat | `POST /webhooks/web/{store_id}` | من المسار | token للجلسة |

**Telegram (1.13):** بوت المتجر الخاص (غير بوت إشعارات المنصة). الربط من الإعدادات: `POST /api/channels/telegram {bot_token}` ← `getMe` ← التوكن بيتخزن مشفّر (Fernet) بـ `store_channels.credentials` ← `setWebhook` على `PUBLIC_BASE_URL/webhooks/telegram/{channel_id}` مع `secret_token` مشتق بـ HMAC من `TELEGRAM_WEBHOOK_SECRET` و `channel_id`. المحادثات الخاصة بس؛ أزرار التأكيد inline keyboard، وكبسة الزر بترجع كنص عنوان الزر. ممنوع نفس البوت لمتجرين، وممنوع بوت الإشعارات.

ملاحظات:
- الـ Webhook لازم يرجع **200 بسرعة**، والمعالجة الثقيلة (LLM) بتصير بـ background task، وإلا القناة ممكن تعيد إرسال نفس الرسالة.
- **منع التكرار (idempotency):** نخزن `external_message_id` بـ `messages.metadata` ونتجاهل الرسالة لو إجت مرتين.
- بالتطوير المحلي: الـ webhooks تحتاج رابط عام، استخدمي نفق (tunnel) مثل ngrok أو cloudflared.

## 4. مسار الرسالة (Message Flow)

`engine/handler.py → handle_inbound(msg)`:

```
1. جيب/أنشئ customer و conversation، واحفظ الرسالة بجدول messages
2. إذا conversation.bot_paused أو status = 'owner'  →  وقّف (صاحب المتجر بيرد). ما في رد آلي.
3. فحص قواعد التحويل الإجبارية (rules.py)، بطريقتين وأي وحدة بتكفي:
      أ) كلمات مفتاحية بعد توحيد النص العربي (بدون LLM، بتشتغل حتى لو الـ LLM فشل)
      ب) حقل sensitive_reason من مكالمة تصنيف النية (الخطوة 5)
      + قواعد بالكود: طلبية كبيرة (حدود stores.large_order_*)، ما في مناطق توصيل مسجلة
      شكوى / طلب خصم / حساسية / "بدي أحكي مع شخص" ...
      ← إذا تطابقت: إنشاء escalation + إشعار صاحب المتجر + رد للعميل "بحوّلك لموظف"، وتوقف
4. اقرأ conversation.bot_state (وين وصلنا بالطلب؟)
5. صنّف نية الرسالة (LLM، Structured Output):
      order | question | confirm | cancel | edit_order | other
      + sensitive_reason (null أو سبب تحويل) ← لو مش null: escalation وتوقف
6. حسب النية:
      order / edit_order / confirm / cancel  →  order_flow.py
      question                                →  engine/qa.py: منتج (سعر/توفر) أو توصيل بالكود من SQL، غير هيك RAG
      other بدون طلب مفتوح (تحية مثلاً)        →  رد ترحيب ثابت (بدون معلومات مخترعة)
      other بنص طلب                           →  إعادة السؤال المفتوح
7. ابعت الرد عبر الـ adapter، واحفظه بجدول messages
```

**أي استثناء أو فشل من الـ LLM (429، timeout، JSON مكسور):** نعمل escalation بدل ما نرد غلط أو نضيّع الرسالة.

## 5. مسار الطلب (Order Flow)

الحالة محفوظة بـ `conversations.bot_state`:

```json
{
  "mode": "idle | collecting | awaiting_confirmation",
  "items": [
    {"variant_id": "uuid", "product_id": "uuid", "product_name": "فستان صيفي", "variant_name": "أحمر / M", "quantity": 2, "notes": null}
  ],
  "queue": [{"product_text": "شنطة", "color": null, "size": null, "quantity": 1}],
  "pending": {"kind": "variant | product | stock | address | zone | phone", "question": "أي المقاس ...؟", "...": "..."},
  "address": "نابلس، شارع ...",
  "zone_id": "uuid", "zone_name": "نابلس",
  "phone": "059..."
}
```

ملاحظات (من تنفيذ 1.8):
- **ما في أسعار بالحالة**: الأسعار والمجموع وأجرة التوصيل بتنحسب من جديد بكل ملخص (`services/order_totals.py` + `pricing.py`)، وبتتخزن كـ snapshot بس عند التأكيد (1.9).
- **سؤال مفتوح واحد بكل مرة** (`pending`). الأجوبة القصيرة (مقاس/لون من الخيارات المعروضة، رقم تلفون، اسم منطقة، نعم/لا) بتنفهم بالكود بدون LLM. غير هيك بنعطي الـ LLM سؤال البوت الأخير كـ context (بيانات مش تعليمات).
- **قفل المحادثة** (`select ... for update`) خلال معالجة الرسالة، فرسالتين ورا بعض ما بيخربوا الحالة.
- **تحويل مبدئي** (`services/escalation.py`): سطر `escalations` + `status = 'waiting_owner'` لفشل الـ LLM، `sensitive_reason`، الأسئلة العامة، ما في مناطق توصيل مسجلة بالمرة، وطلبية كبيرة. منطقة برّا النطاق ما بتتحوّل: البوت بيعتذر، بيذكر المناطق، وبيسكّر الطلب (قرار صاحب المتجر).
- **الكلمات المفتاحية** (`engine/rules.py`، من 1.11): أول خطوة بـ `order_flow.handle` قبل الأزرار والـ LLM: شكوى، خصم/سعر خاص، طلبية كبيرة (جملة)، حساسية/مكونات، طلب موظف، حالة الطلب، وطلب إرجاع/تبديل (`return_request`).

**تعديل الطلب قبل التأكيد (2.6):** لما النية `edit_order` وفي طلب مفتوح، `llm/edits.py` بياخد الطلب كأسطر مرقّمة ورسالة العميل، وبيرجّع عمليات بس: `remove` / `set_quantity` / `add_quantity` / `change_options` (لون/مقاس، الباقي بيضل) / `add_item`. الكود بيطبّقها (`order_flow._edit_order`)، وتغيير اللون/المقاس بيرجع يطابق الـ variant ويفحص المخزون متل منتج جديد، وبعدين ملخص جديد بأسعار محسوبة من جديد. سطر مش موجود بيتجاهل، وما في عمليات مفهومة ← "شو بدك تعدّل بالظبط؟" مع الطلب الحالي.

### خطوات الاستخراج والمطابقة

1. **الاستخراج (LLM):** من رسالة العميل بنطلع JSON بصيغة ثابتة:
   ```json
   {
     "items": [{"product_text": "فستان احمر", "color": "احمر", "size": null, "quantity": 2, "notes": null}],
     "address_text": null,
     "phone": null,
     "wants_to_confirm": false
   }
   ```
2. **المطابقة (كود/SQL، مش LLM):**
   - بحث عن المنتج بـ `search_text` (pg_trgm) + `aliases` بعد توحيد العربي.
   - لو في أكتر من مرشح قريب، بنعطي الـ LLM قائمة المرشحين بس (من الداتا بيز) ليختار منها، أو بنسأل العميل.
   - مطابقة الـ variant من `options` (لون/مقاس).
   - منتج بأكتر من variant والعميل ما حدد ← بنسأل ("أي لون ومقاس؟"). ما بنخمّن.
   - منتج مش موجود ← بنقول للعميل، وبنقترح الأقرب.
3. **الحسابات (كود):** `unit_price = coalesce(variant.price_override, product.price)`، المجموع، أجرة التوصيل من `delivery_zones`. منطقة برّا النطاق ← اعتذار وإغلاق الطلب (ما بنعطي أجرة من راسنا).
4. **اكتمال الطلب:** لو ناقص عنوان أو رقم ← بنسأل. لما يكتمل ← `awaiting_confirmation` وبنعرض ملخص بأزرار (تأكيد / تعديل / إلغاء).
5. **التأكيد:** الأفضل **أزرار** (Telegram inline / WhatsApp reply buttons) بدل ما نفسّر "تمام" بالـ LLM. لو العميل كتب نص، بنصنّفه (yes/no/edit).
6. **التنفيذ (transaction واحدة):**
   ```
   begin
     رقم الطلب = next_order_number(store)
     insert orders + order_items (snapshots)
     لكل variant: update ... where stock_quantity >= qty   (انظر DATABASE.md 5.2)
        └─ لو أي واحد رجّع 0 صفوف → rollback للكل → نبلغ العميل بالمتوفر
     insert inventory_movements (reason = 'order')
   commit
   ```
7. بعد النجاح: رد للعميل برقم الطلب، وإشعار لصاحب المتجر (اختياري)، وتصفير `bot_state`.

## 6. الأسئلة والـ RAG

`engine/qa.py`:

1. توليد embedding لسؤال العميل (OpenAI `text-embedding-3-small`، `dimensions=768`).
2. `match_knowledge(store_id, embedding, 3)`، بالإضافة لبيانات المنتجات (أسعار/توفر بنجيبها بـ SQL مباشرة، مش من الـ KB).
3. **لو أعلى similarity ≥ العتبة:** بنعطي الـ LLM السؤال + المقاطع المسترجعة، مع تعليمات: "جاوب فقط من هالمعلومات، وإذا مش موجود الجواب قول: NOT_FOUND".
4. **لو أقل من العتبة أو رجّع NOT_FOUND:** escalation (`low_confidence`).

**التنفيذ (2.2):** `engine/qa.py` بيمشي بالترتيب:
1. سؤال فيه منتجات (الـ LLM استخرجها): السعر بعد العروض (`pricing.variant_price`)، متوفر/خلصان، والألوان والمقاسات، كله بالكود. ما بينضاف إشي للطلب.
2. سؤال توصيل (كلمات توصيل/شحن): من `delivery_zones` بالكود، مع ذكر التوصيل المجاني إذا في عرض. منطقة مش موجودة ← قائمة المناطق. ما في مناطق بالمرة ← تحويل (`unknown_zone`).
3. غير هيك RAG: أول 3 نتائج، `MIN_SIMILARITY = 0.2` (العتبة الحقيقية هي `found=false` من الـ LLM)، و**فحص بالكود**: أي رقم بجواب الـ LLM مش موجود بالمقاطع ← تحويل. فشل الـ embedding أو الـ LLM ← تحويل (`llm_failure`).
إذا البوت كان ناطر جواب (مقاس، عنوان...) بيرجع يسأله بعد الجواب. قياس `eval/run_rag_eval.py`: 8/8 جواب صح و 8/8 تحويل للأسئلة الي ما إلها جواب (بعد ضبط على نفس المجموعة، فلازم أسئلة جديدة للقياس النهائي).

أنواع المعلومات بالـ Knowledge Base: سياسة التوصيل، أوقات الدوام، سياسة الإرجاع، أي جواب محفوظ من صاحب المتجر.

**الـ Knowledge Base (2.1):** `llm/embeddings.py` (`dimensions=768`، إعادة مرة وحدة) + `services/knowledge.py` + `api/knowledge.py` (`GET/POST/PATCH/DELETE /api/knowledge`، `POST /api/knowledge/upload` لملف .txt: كل فقرة = معلومة، `POST /api/knowledge/search` لتجربة صاحب المتجر).
- الـ embedding من السؤال (اختياري) + الجواب مع بعض، وبيتعاد بس لما النص يتغيّر. إذا فشل الـ embedding ما بينحفظ شي.
- قياس بـ `eval/run_kb_eval.py` (embeddings حقيقية): الصح أول 82%، بأول 3 نتائج 91%. الـ similarity للصح بين 0.33 و 0.63 تقريباً، فعتبة 2.2 لازم تكون منخفضة (حوالي 0.3) ونعطي الـ LLM أول 3 نتائج.

## 7. التحويل لصاحب المتجر والتعلم

### التحويل
1. `escalations` سطر جديد (`open`)، و`conversations.status = 'waiting_owner'`.
2. إشعار Telegram لصاحب المتجر (`owner_telegram_chat_id`): السبب + الرسالة + آخر 10 رسائل + رابط للمحادثة بالداشبورد. بينبعت بعد الـ commit، وفشله ما بيلغي التحويل (`services/notifications.py`).
   - **بوت إشعارات واحد للمنصة** (`TELEGRAM_NOTIFY_BOT_TOKEN`)، مش بوت المتجر تبع العملاء (1.13).
   - **الربط من الإعدادات:** `POST /api/store/telegram/link` بيرجع `t.me/<bot>?start=<code>` (كود موقّع HMAC فيه store_id وصلاحية ساعة، بدون تخزين). صاحب المتجر بيضغط Start، وبعدين `POST /api/store/telegram/verify` بيقرأ `getUpdates` وبيحفظ الـ chat id. ما في webhook ولا رابط عام.
3. بصفحة المحادثات: تاريخ المحادثة كامل + السؤال المحوّل.
4. العميل بيستلم رسالة "بتأكدلك وبرجعلك".

### رد صاحب المتجر
1. بيكتب الجواب بالويب، وبيختار:
   - **"احفظ هالجواب للبوت"** → `save_to_kb = true`
   - **"جواب خاص بهالعميل بس"** → `save_to_kb = false`
2. الجواب بيتبعت للعميل عبر نفس القناة (`messages.sender = 'owner'`).
3. لو `save_to_kb = true`: نعمل embedding للسؤال والجواب ونضيفهم لـ `knowledge_base` (`source = 'owner_answer'`)، ويفضل صاحب المتجر يقدر يعدّل صياغة السؤال/الجواب قبل الحفظ.
4. `escalation.status = 'answered'`، وبترجع المحادثة `bot` (أو بتضل `owner` حسب اختيار صاحب المتجر).
5. **التنفيذ (2.3):** `POST /api/escalations/{id}/answer {answer, save_to_kb, then}`، وصندوق الجواب جوا صفحة المحادثات. `save_to_kb` إجباري بكل جواب (زرين: "أرسل كجواب خاص" / "أرسل واحفظه للبوت"). `then = bot` بيرجّع البوت بس إذا ما ضل أسئلة مفتوحة بنفس المحادثة. الإرسال من `services/owner_messages.py` (نفس الرد العادي): بيتخزن داخل الـ transaction وبينبعت بعد الـ commit. الإضافة الفعلية للـ KB بـ 2.4.
6. **التعلّم (2.4):** مع `save_to_kb = true` الصفحة بتعرض خطوة صياغة (`kb_question` / `kb_answer`، معبّاين مسبقاً بسؤال العميل وجواب صاحب المتجر). الـ embedding بيصير **قبل** الإرسال: إذا فشل ما بينبعت ولا بينحفظ شي (502). بعدها الجواب وسطر `knowledge_base` (`source = owner_answer`، `escalation_id`) بنفس الـ transaction. الحفظ مسموح بس لـ `not_in_data` و `llm_failure`: باقي القواعد (شكوى، خصم، حساسية، طلب موظف...) بتتحوّل دايماً بالكلمات المفتاحية، فالحفظ ما إله فايدة أو بيكسر القاعدة 5 (422).

### الإيقاف اليدوي (1.12)
- زر "أوقف البوت" بالمحادثة بيحط `bot_paused = true`.
- رد صاحب المتجر (`POST /api/conversations/{id}/reply`) بيتخزن `sender = 'owner'`، بينبعت عبر adapter القناة (`channels/registry.py`)، وبيحط `status = 'owner'`: البوت بيسكت لحد ما صاحب المتجر يرجّعه.
- "رجّع البوت" (`/resume`) بيرجّع `status = 'bot'` و `bot_paused = false`. إذا في طلب مفتوح، صاحب المتجر بيختار: **كمّل نفس الطلب** (`keep_order = true`) أو **ابدأ من جديد** (بيمسح `bot_state`). الطلبات المؤكدة ما بتتأثر.
- الاستئناف ما بيسكّر سطور `escalations`: الجواب عليها وخيار "احفظ للبوت" بصفحة الأسئلة المحولة (2.3).

## 8. شات صاحب المتجر (تعديل المخزون)

مبني على **Function Calling**، وبمبدأ **اقتراح ثم تأكيد**:

```
صاحب المتجر: "نقّص كمية الفستان الأحمر M بمقدار 5"
      │
      ▼
LLM يستدعي أداة:  propose_stock_adjustment(product_text, color, size, change | set_to)
      │
      ▼
الكود: يطابق المنتج/الـ variant بـ SQL → يرجع "إجراء معلّق" (pending action) بدون تنفيذ
      │
      ▼
الواجهة تعرض: "رح أنقص 5 من فستان صيفي (أحمر / M): الكمية من 10 إلى 5. موافق؟"   [تأكيد] [إلغاء]
              (ولو الكمية الناتجة سالبة، الكود بيرفض الإجراء ويشرح السبب بدل ما يعرض زر تأكيد)
      │ (ضغط تأكيد)
      ▼
endpoint منفصل يتحقق من الـ pending action وينفذه (inventory_movements, reason = 'chat_edit')
```

الأدوات المقترحة: `search_products`، `propose_stock_adjustment`، `propose_price_change`، `get_stock_status`، `get_sales_summary` (قراءة فقط).
المنفّذ بالمهمة 3.3: `propose_stock_adjustment` و `get_stock_status`. الإجراء المعلّق بينحفظ بـ `owner_pending_actions` وبتنتهي صلاحيته بعد 15 دقيقة.
**ممنوع** أي أداة تعدّل مباشرة بدون خطوة تأكيد.

## 9. الـ API لصاحب المتجر (مختصر)

| Method | Path | وظيفة |
|---|---|---|
| POST | `/api/products/import` | رفع Excel (معاينة ثم تأكيد) |
| GET/PATCH | `/api/products`, `/api/variants/{id}` | عرض وتعديل المنتجات والمخزون |
| GET | `/api/orders` | جدول الطلبات (فلترة وبحث) |
| PATCH | `/api/orders/{id}/status` | تغيير حالة الطلب |
| GET | `/api/conversations`, `/api/conversations/{id}/messages` | المحادثات |
| POST | `/api/conversations/{id}/pause` / `/resume` | إيقاف/تشغيل البوت |
| POST | `/api/conversations/{id}/reply` | رد صاحب المتجر |
| GET | `/api/escalations?status=open` | الأسئلة المحولة |
| POST | `/api/escalations/{id}/answer` | جواب + `save_to_kb` |
| GET/POST/PATCH | `/api/knowledge` | إدارة الـ Knowledge Base |
| GET | `/api/reports/sales`, `/api/reports/low-stock`, `/api/reports/forecast` | التقارير |
| POST | `/api/owner-chat` | شات المخزون (يرجع pending action) |
| POST | `/api/owner-chat/confirm/{action_id}` | تنفيذ الإجراء المعلّق |
| POST | `/api/owner-chat/cancel/{action_id}` | إلغاء الإجراء المعلّق |
| GET/POST | `/api/channels` | ربط القنوات |

كل الـ routes بتتحقق من JWT (Supabase) وبتجيب `store_id` من المستخدم، **مش من الـ request body**.

## 10. طبقة الـ LLM

كل الكود الي بيحكي مع OpenAI موجود بـ `llm/` فقط (مكتبة `openai` الرسمية):
- **Structured Output** بـ Pydantic schemas، وبنتحقق من الناتج (validate)، ولو فشل بنعيد مرة وحدة، وبعدها escalation.
- **Retries** مع exponential backoff لأخطاء 429/5xx.
- **Timeouts** واضحة.
- **Logging**: الـ prompt المختصر، الناتج، الزمن، الأخطاء (بدون بيانات حساسة).
- **تغيير الموديل** بمتغير بيئة (`OPENAI_MODEL`، `OPENAI_EMBED_MODEL`)، عشان لو تغيرت الأسعار أو الأسماء ما نعدّل الكود.
- **التكلفة**: الـ API مدفوع حسب الاستخدام. الـ unit tests بتستخدم mock، والـ eval بيتشغل عند الحاجة بس.
- بالتطوير: تجنبي بيانات عملاء حقيقية.

## 11. الجدولة (Background Jobs)

| المهمة | التكرار |
|---|---|
| تحديث تنبؤ نفاد المخزون | مع كل طلب للصفحة (3.4، ما في داعي لـ job) |
| توليد embeddings لإدخالات الـ KB الجديدة | عند الإضافة |
| تنظيف جلسات الويب القديمة | يومياً |

للـ MVP يكفي `APScheduler` داخل الـ backend أو `FastAPI BackgroundTasks`.

## 12. التنبؤ بنفاد المخزون

- **الأساس:** `أيام متبقية = stock_quantity ÷ متوسط المبيعات اليومية` (من `inventory_movements` بسبب `order` آخر 14-30 يوم) لكل variant.
- **لاحقاً:** موديل Time Series (مثلاً Prophet) لو توفرت بيانات كافية. ما بنعتمد عليه إلا بعد قياس إنه أحسن من الأساس.
- بيانات تجريبية (synthetic) بسكربت لتوليد طلبات سابقة.
- **المنفّذ (3.4):** `services/forecasting.py`. نافذة 28 يوم، والمتوسط بينقسم على أيام التاريخ الفعلية للـ variant (أقل شي يوم، أكتر شي 28) حتى المنتج الجديد ما يتظلم. المبيع = `order` ناقص `order_cancel`. الـ variants الخلصانة أو يلي ما انباع منها شي ما إلها تنبؤ. بينحسب مع كل طلب بدل job يومي (الـ query خفيف ودايماً محدّث).
- `GET /api/reports/forecast?days=N` (1-90، الافتراضي 14) بيرجع يلي متوقع يخلص خلال N يوم، الأقرب أولاً، وبينعرض بالـ Dashboard.
- سكربت البيانات التجريبية: `backend/scripts/seed_sales.py` (طلبات `delivered` بالماضي لعملاء `synthetic-…`، ما بيغيّر المخزون الحالي، و`--clear` بيمسحها).

## 13. الأمان

- **Secrets**: `.env` بالتطوير، وSecrets الخدمة بالنشر. لا شي بالـ Git.
- **Service Role key**: الـ backend بس. كل query بيفلتر بـ `store_id`.
- **RLS** على كل الجداول (للطلبات القادمة من الـ Frontend مباشرة).
- **Webhooks**: التحقق من سر القناة/التوقيع قبل المعالجة.
- **Rate limiting** بسيط على endpoints الـ webhooks والـ owner-chat.
- **Prompt injection**: رسائل العملاء بتعتبر بيانات مش تعليمات. بالـ prompts نفصل تعليمات النظام عن نص العميل، والـ LLM ما عنده صلاحيات تنفيذ (الكود بيتحقق وبينفذ).
- **بيانات العملاء**: أقل ما يمكن، وما بنخزن إلا الي لازم.

## 14. النشر (مبدئياً)

- **Frontend**: استضافة ستاتيك (Vercel/Netlify أو شبههم).
- **Backend**: خدمة بتشغّل FastAPI (Render/Railway/Fly.io أو شبههم).
- **DB/Auth**: Supabase.
- الخطط المجانية وحدودها بتتغير، وبعضها بيوقف الخدمة بعد خمول، فتأكدي من كل واحدة قبل أي عرض أو مقابلة.

## 15. الاختبار والتقييم

- **Unit tests**: `product_matching`، حساب المجاميع، خصم المخزون (حالة التزامن وعدم كفاية الكمية)، `arabic.py`، `excel_import`.
- **Integration tests**: تدفق الطلب كامل بمحاكي الويب (بدون LLM حقيقي، باستخدام mock).
- **Eval للاستخراج** (`eval/`): مجموعة رسائل عربية بلهجات وأخطاء إملائية مع الناتج المتوقع، ونقيس دقة كل حقل (المنتج، الـ variant، الكمية، العنوان).
- **Eval للتحويل**: رسائل شكوى/حساسية/خصم لازم **كلها** تتحول.
- **Eval للـ RAG**: أسئلة موجودة جوابها وأسئلة مش موجودة (لازم يحول).
