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
│  │ Engine        │─►│ orders    │  │ (Gemini wrapper)    │  │
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
│   │   ├── llm/                     # كل التعامل مع Gemini هون فقط
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
      + قواعد بالكود: منطقة غير مسجلة، طلبية كبيرة (حدود stores.large_order_*)
      شكوى / طلب خصم / حساسية / "بدي أحكي مع شخص" ...
      ← إذا تطابقت: إنشاء escalation + إشعار صاحب المتجر + رد للعميل "بحوّلك لموظف"، وتوقف
4. اقرأ conversation.bot_state (وين وصلنا بالطلب؟)
5. صنّف نية الرسالة (LLM، Structured Output):
      order | question | confirm | cancel | edit_order | other
      + sensitive_reason (null أو سبب تحويل) ← لو مش null: escalation وتوقف
6. حسب النية:
      order / edit_order / confirm / cancel  →  order_flow.py
      question                                →  qa.py (RAG)
      other / مش متأكد                        →  escalation
7. ابعت الرد عبر الـ adapter، واحفظه بجدول messages
```

**أي استثناء أو فشل من الـ LLM (429، timeout، JSON مكسور):** نعمل escalation بدل ما نرد غلط أو نضيّع الرسالة.

## 5. مسار الطلب (Order Flow)

الحالة محفوظة بـ `conversations.bot_state`:

```json
{
  "mode": "idle | collecting | awaiting_confirmation",
  "draft": {
    "items": [
      {"variant_id": "uuid", "product_name": "فستان صيفي", "variant_name": "أحمر / M", "quantity": 2, "unit_price": 120.0, "notes": null}
    ],
    "address": "نابلس، شارع ...",
    "phone": "059...",
    "delivery_zone_id": "uuid",
    "delivery_fee": 10.0
  },
  "pending_questions": ["variant_for:فستان صيفي"]
}
```

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
3. **الحسابات (كود):** `unit_price = coalesce(variant.price_override, product.price)`، المجموع، أجرة التوصيل من `delivery_zones`. منطقة غير موجودة ← escalation (ما بنعطي أجرة من راسنا).
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

1. توليد embedding لسؤال العميل (Gemini Embedding).
2. `match_knowledge(store_id, embedding, 3)`، بالإضافة لبيانات المنتجات (أسعار/توفر بنجيبها بـ SQL مباشرة، مش من الـ KB).
3. **لو أعلى similarity ≥ العتبة:** بنعطي الـ LLM السؤال + المقاطع المسترجعة، مع تعليمات: "جاوب فقط من هالمعلومات، وإذا مش موجود الجواب قول: NOT_FOUND".
4. **لو أقل من العتبة أو رجّع NOT_FOUND:** escalation (`low_confidence`).

أنواع المعلومات بالـ Knowledge Base: سياسة التوصيل، أوقات الدوام، سياسة الإرجاع، أي جواب محفوظ من صاحب المتجر.

## 7. التحويل لصاحب المتجر والتعلم

### التحويل
1. `escalations` سطر جديد (`open`)، و`conversations.status = 'waiting_owner'`.
2. إشعار Telegram لصاحب المتجر (`owner_telegram_chat_id`): السؤال + رابط للمحادثة بالداشبورد.
3. بصفحة المحادثات: تاريخ المحادثة كامل + السؤال المحوّل.
4. العميل بيستلم رسالة "بتأكدلك وبرجعلك".

### رد صاحب المتجر
1. بيكتب الجواب بالويب، وبيختار:
   - **"احفظ هالجواب للبوت"** → `save_to_kb = true`
   - **"جواب خاص بهالعميل بس"** → `save_to_kb = false`
2. الجواب بيتبعت للعميل عبر نفس القناة (`messages.sender = 'owner'`).
3. لو `save_to_kb = true`: نعمل embedding للسؤال والجواب ونضيفهم لـ `knowledge_base` (`source = 'owner_answer'`)، ويفضل صاحب المتجر يقدر يعدّل صياغة السؤال/الجواب قبل الحفظ.
4. `escalation.status = 'answered'`، وبترجع المحادثة `bot` (أو بتضل `owner` حسب اختيار صاحب المتجر).

### الإيقاف اليدوي
زر "أوقف البوت" بالمحادثة بيحط `bot_paused = true`.

## 8. شات صاحب المتجر (تعديل المخزون)

مبني على **Function Calling**، وبمبدأ **اقتراح ثم تأكيد**:

```
صاحب المتجر: "نقّص كمية الفستان الأحمر M بمقدار 5"
      │
      ▼
LLM يستدعي أداة:  propose_stock_adjustment(product_text, variant_hint, delta)
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
| GET/POST | `/api/channels` | ربط القنوات |

كل الـ routes بتتحقق من JWT (Supabase) وبتجيب `store_id` من المستخدم، **مش من الـ request body**.

## 10. طبقة الـ LLM

كل الكود الي بيحكي مع Gemini موجود بـ `llm/` فقط:
- **Structured Output** بـ Pydantic schemas، وبنتحقق من الناتج (validate)، ولو فشل بنعيد مرة وحدة، وبعدها escalation.
- **Retries** مع exponential backoff لأخطاء 429/5xx.
- **Timeouts** واضحة.
- **Logging**: الـ prompt المختصر، الناتج، الزمن، الأخطاء (بدون بيانات حساسة).
- **تغيير الموديل** بمتغير بيئة، عشان لو تغيرت الحدود المجانية أو الأسماء ما نعدّل الكود.
- بالخطة المجانية: تجنبي بيانات عملاء حقيقية.

## 11. الجدولة (Background Jobs)

| المهمة | التكرار |
|---|---|
| تحديث تنبؤ نفاد المخزون | يومياً |
| توليد embeddings لإدخالات الـ KB الجديدة | عند الإضافة |
| تنظيف جلسات الويب القديمة | يومياً |

للـ MVP يكفي `APScheduler` داخل الـ backend أو `FastAPI BackgroundTasks`.

## 12. التنبؤ بنفاد المخزون

- **الأساس:** `أيام متبقية = stock_quantity ÷ متوسط المبيعات اليومية` (من `inventory_movements` بسبب `order` آخر 14-30 يوم) لكل variant.
- **لاحقاً:** موديل Time Series (مثلاً Prophet) لو توفرت بيانات كافية. ما بنعتمد عليه إلا بعد قياس إنه أحسن من الأساس.
- بيانات تجريبية (synthetic) بسكربت لتوليد طلبات سابقة.

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
