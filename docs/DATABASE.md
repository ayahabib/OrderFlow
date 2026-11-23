# DATABASE.md: تصميم قاعدة البيانات

> PostgreSQL على Supabase + pgvector. النسخة 0.2 (مسودة): أضفنا الـ Variants (ألوان/مقاسات).

## 1. المبادئ الأساسية

1. **كل جدول فيه `store_id`** (Multi-tenancy). هيك كل متجر بيشوف بياناته بس.
2. **Row Level Security (RLS)** مفعّل على كل الجداول.
3. **الـ backend (FastAPI) بيستخدم Service Role** لاستقبال رسائل واتساب/تيليجرام لأنه ما في مستخدم مسجل دخول وقتها. وهاد بيتجاوز RLS، فلازم **كل query بالكود يفلتر بـ `store_id`**.
4. **الأسعار والمخزون بالكود/SQL**، الـ LLM ما بيحسب.
5. **تحديث المخزون ذري (atomic)** عشان ما يصير بيع زيادة.
6. أسماء المنتجات والأسعار بتتنسخ داخل `order_items` (snapshot) عشان الطلبات القديمة ما تتغير لو تغير سعر المنتج.
7. **كل منتج إله variant واحد على الأقل.** المنتج البسيط (بدون ألوان/مقاسات) بيكون إله variant واحد اسمه "الافتراضي". هيك المخزون موجود بمكان واحد فقط (`product_variants`) والكود ما بيحتاج مسارين.

## 2. الجداول والعلاقات

```
auth.users (Supabase)
    │ 1:1
    ▼
stores ──┬── store_channels
         ├── products ── product_variants ── inventory_movements
         ├── delivery_zones
         ├── customers ── conversations ── messages
         │                     │
         │                     ├── escalations ──► knowledge_base
         │                     └── orders ── order_items ──► product_variants
         └── knowledge_base
```

| الجدول | الوظيفة |
|---|---|
| `stores` | المتجر، مرتبط بمستخدم واحد |
| `store_channels` | القنوات المربوطة (WhatsApp / Telegram / Web) |
| `products` | المنتج (الاسم، الوصف، التصنيف، السعر الأساسي) |
| `product_variants` | النسخ (لون/مقاس...)، وفيها **الكمية** والسعر الخاص (اختياري) |
| `inventory_movements` | سجل كل تغيير بالمخزون (مهم للتنبؤ والتقارير) |
| `delivery_zones` | مناطق التوصيل وأجرتها |
| `customers` | العملاء (لكل متجر ولكل قناة) |
| `conversations` | المحادثات وحالتها وحالة البوت (`bot_state`) |
| `messages` | الرسائل |
| `orders` / `order_items` | الطلبات وتفاصيلها |
| `escalations` | الأسئلة المحولة لصاحب المتجر وجوابه |
| `knowledge_base` | الأسئلة/الأجوبة والمعلومات الي البوت بيرد منها (RAG) |
| `owner_pending_actions` | تعديلات المخزون المقترحة من شات صاحب المتجر، بتستنى التأكيد (3.3) |

### الفرق بين Variant وخيار نصي (notes)
- **Variant**: صفة بتأثر على المخزون (لون، مقاس). كل variant إله كمية. مثال: "فستان أحمر M".
- **notes بالطلب**: ملاحظة حرة ما بتأثر على المخزون. مثال: "حار"، "بدون بصل"، "تغليف هدية".

## 3. SQL

### 3.1 الإضافات والأنواع

```sql
create extension if not exists vector;
create extension if not exists pg_trgm;

create type channel_type        as enum ('whatsapp', 'telegram', 'web');
create type conversation_status as enum ('bot', 'waiting_owner', 'owner', 'closed');
create type message_sender      as enum ('customer', 'bot', 'owner');
create type order_status        as enum ('new', 'in_progress', 'delivered', 'cancelled');
create type escalation_reason   as enum ('low_confidence', 'forced_rule', 'customer_requested');
create type escalation_status   as enum ('open', 'answered', 'dismissed');
create type kb_source           as enum ('upload', 'owner_answer', 'manual');
create type movement_reason     as enum ('upload', 'order', 'order_cancel', 'manual', 'chat_edit');
```

### 3.2 المتجر والقنوات

```sql
create table stores (
  id                      uuid primary key default gen_random_uuid(),
  owner_id                uuid not null unique references auth.users(id) on delete cascade, -- مستخدم واحد لكل متجر
  name                    text not null,
  phone                   text,
  currency                text not null default 'ILS',
  timezone                text not null default 'Asia/Hebron',
  owner_telegram_chat_id  bigint,                 -- لإرسال الإشعارات لصاحب المتجر
  next_order_number       integer not null default 1,
  -- حدود "الطلبية الكبيرة" (تحويل إجباري لو تجاوز أي حد منها)
  large_order_item_qty    integer not null default 10   check (large_order_item_qty > 0),    -- كمية بند واحد
  large_order_total_qty   integer not null default 20   check (large_order_total_qty > 0),   -- مجموع القطع
  large_order_amount      numeric(10,2) not null default 1000 check (large_order_amount > 0), -- إجمالي الطلب
  created_at              timestamptz not null default now()
);

create table store_channels (
  id            uuid primary key default gen_random_uuid(),
  store_id      uuid not null references stores(id) on delete cascade,
  channel       channel_type not null,
  external_id   text,           -- WhatsApp phone_number_id أو Telegram bot id
  credentials   text,           -- مشفّر بـ Fernet بالـ backend (المفتاح CHANNEL_ENCRYPTION_KEY بـ .env)، أبداً نص عادي
  is_active     boolean not null default true,
  created_at    timestamptz not null default now(),
  unique (store_id, channel)
);
```

### 3.3 المنتجات والـ Variants والمخزون

```sql
create table products (
  id           uuid primary key default gen_random_uuid(),
  store_id     uuid not null references stores(id) on delete cascade,
  name         text not null,
  search_text  text,                         -- الاسم بعد توحيد الحروف العربية (أ/ا، ة/ه...) للبحث
  aliases      text[] not null default '{}', -- أسماء بديلة ("فستان احمر"، "الفستان الاحمر")
  description  text,
  category     text,
  price        numeric(10,2) not null check (price >= 0),   -- السعر الأساسي
  is_active    boolean not null default true,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (store_id, name)
);

create index products_store_idx on products (store_id);
create index products_search_trgm on products using gin (search_text gin_trgm_ops);

-- مطابقة المنتجات (1.7): normalize_ar نسخة SQL من utils/arabic.py (في test بيتأكد إنهم متطابقين)،
-- و alias_text الأسماء البديلة بعد التوحيد، بتنحسب لحالها من aliases.
alter table products
  add column alias_text text generated always as (alias_search_text(aliases)) stored;
create index products_alias_trgm on products using gin (alias_text gin_trgm_ops);

create table product_variants (
  id                   uuid primary key default gen_random_uuid(),
  store_id             uuid not null references stores(id) on delete cascade,
  product_id           uuid not null references products(id) on delete cascade,
  name                 text not null default 'الافتراضي',   -- مثلاً "أحمر / M"
  options              jsonb not null default '{}',          -- {"color": "أحمر", "size": "M"}
  sku                  text,
  price_override       numeric(10,2) check (price_override is null or price_override >= 0),
  stock_quantity       integer not null default 0 check (stock_quantity >= 0),
  low_stock_threshold  integer not null default 5,
  is_active            boolean not null default true,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  unique (product_id, name)
);

create index variants_product_idx on product_variants (product_id);
create index variants_store_idx on product_variants (store_id);
create unique index variants_sku_idx on product_variants (store_id, sku) where sku is not null;

create table inventory_movements (
  id          uuid primary key default gen_random_uuid(),
  store_id    uuid not null references stores(id) on delete cascade,
  variant_id  uuid not null references product_variants(id) on delete cascade,
  change      integer not null,          -- سالب = نقص، موجب = زيادة
  reason      movement_reason not null,
  order_id    uuid,                      -- بنربطه بالطلب لو الحركة من طلب
  created_at  timestamptz not null default now()
);

create index movements_variant_time_idx on inventory_movements (variant_id, created_at);

create table delivery_zones (
  id        uuid primary key default gen_random_uuid(),
  store_id  uuid not null references stores(id) on delete cascade,
  name      text not null,
  fee       numeric(10,2) not null check (fee >= 0),
  is_active boolean not null default true,
  unique (store_id, name)
);
```

**السعر الفعلي للـ variant** = `coalesce(price_override, products.price)`.

### 3.4 العملاء والمحادثات

```sql
create table customers (
  id               uuid primary key default gen_random_uuid(),
  store_id         uuid not null references stores(id) on delete cascade,
  channel          channel_type not null,
  external_id      text not null,        -- رقم واتساب أو Telegram chat id
  name             text,
  phone            text,
  default_address  text,
  created_at       timestamptz not null default now(),
  unique (store_id, channel, external_id)
);

create table conversations (
  id               uuid primary key default gen_random_uuid(),
  store_id         uuid not null references stores(id) on delete cascade,
  customer_id      uuid not null references customers(id) on delete cascade,
  channel          channel_type not null,
  status           conversation_status not null default 'bot',
  bot_paused       boolean not null default false,   -- إيقاف الرد الآلي
  bot_state        jsonb not null default '{}',      -- مسودة الطلب وحالة المحادثة (انظر ARCHITECTURE.md)
  last_message_at  timestamptz not null default now(),
  created_at       timestamptz not null default now()
);

create index conversations_store_status_idx on conversations (store_id, status, last_message_at desc);

create table messages (
  id               uuid primary key default gen_random_uuid(),
  store_id         uuid not null references stores(id) on delete cascade,
  conversation_id  uuid not null references conversations(id) on delete cascade,
  sender           message_sender not null,
  content          text not null,
  metadata         jsonb not null default '{}',  -- JSON الاستخراج، درجة الثقة، رقم رسالة القناة...
  created_at       timestamptz not null default now()
);

create index messages_conv_time_idx on messages (conversation_id, created_at);
```

### 3.5 الطلبات

```sql
create table orders (
  id               uuid primary key default gen_random_uuid(),
  store_id         uuid not null references stores(id) on delete cascade,
  order_number     integer not null,             -- رقم يظهر للعميل، متسلسل لكل متجر
  customer_id      uuid not null references customers(id),
  conversation_id  uuid references conversations(id),
  status           order_status not null default 'new',
  delivery_address text,
  delivery_zone_id uuid references delivery_zones(id),
  delivery_fee     numeric(10,2) not null default 0,
  subtotal         numeric(10,2) not null,
  total            numeric(10,2) not null,
  notes            text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (store_id, order_number)
);

create index orders_store_status_idx on orders (store_id, status, created_at desc);

create table order_items (
  id            uuid primary key default gen_random_uuid(),
  store_id      uuid not null references stores(id) on delete cascade,
  order_id      uuid not null references orders(id) on delete cascade,
  product_id    uuid references products(id) on delete set null,
  variant_id    uuid references product_variants(id) on delete set null,
  product_name  text not null,                   -- snapshot
  variant_name  text,                            -- snapshot ("أحمر / M")
  unit_price    numeric(10,2) not null,          -- snapshot
  quantity      integer not null check (quantity > 0),
  notes         text                             -- ملاحظة حرة ما بتأثر على المخزون ("حار"، "بدون بصل")
);

create index order_items_order_idx on order_items (order_id);
```

> مع العروض (3.7): `order_items.unit_price` = السعر بعد العرض، ومنضيف `list_price numeric(10,2)` (السعر قبل العرض) و `offer_id uuid references offers(id) on delete set null` كـ snapshot. و`orders` بيضل فيه `delivery_fee` (صفر لو انطبق توصيل مجاني) + `free_delivery_offer_id`.

### 3.7 العروض

```sql
create type offer_kind as enum ('percent_off', 'fixed_price', 'free_delivery');

create table offers (
  id               uuid primary key default gen_random_uuid(),
  store_id         uuid not null references stores(id) on delete cascade,
  name             text not null,                       -- "خصم الصيف"
  kind             offer_kind not null,
  product_id       uuid references products(id) on delete cascade,          -- عرض على منتج كامل
  variant_id       uuid references product_variants(id) on delete cascade,  -- أو على نسخة وحدة
  percent          numeric(5,2) check (percent > 0 and percent < 100),      -- percent_off
  fixed_price      numeric(10,2) check (fixed_price >= 0),                  -- fixed_price
  min_order_total  numeric(10,2) check (min_order_total >= 0),              -- free_delivery
  starts_at        timestamptz,                         -- null = من هلأ
  ends_at          timestamptz,                         -- null = بدون نهاية
  is_active        boolean not null default true,
  created_at       timestamptz not null default now(),
  check (ends_at is null or starts_at is null or ends_at > starts_at),
  check (
    (kind = 'percent_off'   and percent is not null and fixed_price is null and min_order_total is null
                            and num_nonnulls(product_id, variant_id) = 1) or
    (kind = 'fixed_price'   and fixed_price is not null and percent is null and min_order_total is null
                            and num_nonnulls(product_id, variant_id) = 1) or
    (kind = 'free_delivery' and min_order_total is not null and percent is null and fixed_price is null
                            and product_id is null and variant_id is null)
  )
);

create index offers_store_idx on offers (store_id, is_active);
```

قواعد الحساب (بالكود، `services/pricing.py`):
- السعر الأساسي للنسخة = `coalesce(price_override, products.price)`.
- العروض الفعّالة = `is_active` و `now()` بين `starts_at` و `ends_at`، وعلى نفس النسخة أو منتجها.
- `percent_off`: `round(base × (1 − percent/100), 2)` (تقريب نص لفوق). `fixed_price`: `min(fixed_price, base)`.
- بنختار العرض الي بيعطي **أقل سعر**، عرض واحد بس.
- `free_delivery`: لو مجموع الطلب بعد الخصومات ≥ `min_order_total` ← أجرة التوصيل = 0.

### 3.6 التحويل والـ Knowledge Base

```sql
create table escalations (
  id               uuid primary key default gen_random_uuid(),
  store_id         uuid not null references stores(id) on delete cascade,
  conversation_id  uuid not null references conversations(id) on delete cascade,
  question         text not null,
  reason           escalation_reason not null,
  rule             text,                         -- السبب التفصيلي: complaint, discount, large_order, allergy_ingredients,
                                                 -- unknown_zone, human_requested, not_in_data, order_status,
                                                 -- return_request, llm_failure
  status           escalation_status not null default 'open',
  owner_answer     text,
  save_to_kb       boolean,                      -- true = "احفظ للبوت"، false = "جواب خاص"
  created_at       timestamptz not null default now(),
  answered_at      timestamptz
);

create index escalations_store_status_idx on escalations (store_id, status, created_at desc);

create table knowledge_base (
  id            uuid primary key default gen_random_uuid(),
  store_id      uuid not null references stores(id) on delete cascade,
  question      text,
  answer        text not null,
  source        kb_source not null,
  escalation_id uuid references escalations(id) on delete set null,
  embedding     vector(768),                     -- البعد لازم يطابق موديل الـ embedding
  is_active     boolean not null default true,
  created_at    timestamptz not null default now()
);

create index kb_embedding_idx on knowledge_base using hnsw (embedding vector_cosine_ops);
create index kb_store_idx on knowledge_base (store_id);
```

> **ملاحظة:** البعد `768` لأننا بنطلب من OpenAI `text-embedding-3-small` بـ `dimensions=768` (الافتراضي 1536). لو غيّرتي الموديل أو البعد، لازم migration جديدة للعمود والدالة `match_knowledge`.

### 3.8 شات المخزون: الإجراءات المعلّقة (3.3)

الـ LLM بس بيقترح. الاقتراح بينحفظ هون `pending`، والمخزون بيتغيّر بس من endpoint التأكيد (ARCHITECTURE.md قسم 8).

```sql
create type pending_action_status as enum ('pending', 'confirmed', 'cancelled');

create table owner_pending_actions (
  id           uuid primary key default gen_random_uuid(),
  store_id     uuid not null references stores(id) on delete cascade,
  variant_id   uuid not null references product_variants(id) on delete cascade,
  change       integer check (change <> 0),          -- "نقّص 5" = -5
  set_to       integer check (set_to >= 0),          -- "خلّيه 7" = 7
  status       pending_action_status not null default 'pending',
  created_at   timestamptz not null default now(),
  expires_at   timestamptz not null default now() + interval '15 minutes',
  resolved_at  timestamptz,
  constraint owner_pending_actions_one_amount check (num_nonnulls(change, set_to) = 1)
);
```

عند التأكيد: نفس الـ transaction بتقفل الـ variant، بتحسب الكمية الجديدة من المخزون **الحالي** (لو إجا طلب بالنص)، بترفض لو النتيجة سالبة، وبتكتب `inventory_movements` بـ `reason = 'chat_edit'`.

## 4. رفع Excel مع الـ Variants

الأعمدة المقبولة:

| العمود | مطلوب | ملاحظة |
|---|---|---|
| `product_name` | نعم | الصفوف الي لها نفس الاسم بتنجمع تحت منتج واحد |
| `price` | نعم | السعر الأساسي للمنتج |
| `quantity` | نعم | الكمية لهالـ variant |
| `color` | لا | |
| `size` | لا | |
| `variant_price` | لا | بيتخزن بـ `price_override` |
| `sku` | لا | |
| `category` / `description` | لا | |
| `aliases` | لا | أسماء بديلة مفصولة بـ `\|` (مثلاً `شنطة\|شنطه\|حقيبة يد`)، بتتقرأ من أول صف للمنتج وبتتخزن بـ `products.aliases` |

مثال:

| product_name | price | color | size | quantity |
|---|---|---|---|---|
| فستان صيفي | 120 | أحمر | M | 4 |
| فستان صيفي | 120 | أحمر | L | 2 |
| فستان صيفي | 120 | أزرق | M | 5 |
| حقيبة جلد | 90 | | | 7 |

النتيجة: منتج "فستان صيفي" بـ 3 variants (أحمر/M، أحمر/L، أزرق/M)، ومنتج "حقيبة جلد" بـ variant واحد اسمه "الافتراضي".

قواعد الرفع:
- اسم الـ variant = دمج الخيارات المعبّاة ("أحمر / M")، وإذا ما في خيارات يكون "الافتراضي".
- لو نفس (المنتج + الـ variant) موجود، بنحدّث الكمية (مع تسجيل حركة `upload`).
- صف فيه خطأ (سعر مش رقم، كمية سالبة) بيظهر بالمعاينة ولا بيتخزن.
- المنتج الي مالو ولا variant مش مسموح، الكود لازم دايماً ينشئ واحد على الأقل.

## 5. الدوال المهمة

### 5.1 رقم الطلب المتسلسل لكل متجر

```sql
create or replace function next_order_number(p_store_id uuid)
returns integer language sql as $$
  update stores
     set next_order_number = next_order_number + 1
   where id = p_store_id
  returning next_order_number - 1;
$$;
```

### 5.2 خصم المخزون بشكل ذري (يمنع البيع الزيادة)

```sql
-- لكل variant بالطلب. لو رجّع 0 صفوف = الكمية مش كافية والطلب لازم يفشل.
update product_variants
   set stock_quantity = stock_quantity - :qty,
       updated_at = now()
 where id = :variant_id
   and store_id = :store_id
   and is_active
   and stock_quantity >= :qty;
```

مهم: تأكيد الطلب كله (إنشاء الطلب + خصم المخزون لكل الـ variants + تسجيل الحركات) لازم يتم داخل **transaction واحدة**. لو أي variant ما كفى، بنعمل rollback للكل.

### 5.3 البحث بالمعاني بالـ Knowledge Base

```sql
create or replace function match_knowledge(
  p_store_id uuid,
  query_embedding vector(768),
  match_count int default 3
)
returns table (id uuid, question text, answer text, similarity float)
language sql stable as $$
  select kb.id, kb.question, kb.answer,
         1 - (kb.embedding <=> query_embedding) as similarity
    from knowledge_base kb
   where kb.store_id = p_store_id
     and kb.is_active
   order by kb.embedding <=> query_embedding
   limit match_count;
$$;
```

عتبة الثقة (similarity threshold) بتحددي بالكود: لو أعلى نتيجة أقل من العتبة، البوت بيحوّل لصاحب المتجر.

## 6. الأمان: Row Level Security

```sql
create or replace function current_store_id()
returns uuid language sql stable security definer set search_path = public as $$
  select id from stores where owner_id = auth.uid()
$$;

-- جدول stores
alter table stores enable row level security;
create policy "owner_access" on stores for all
  using (owner_id = auth.uid()) with check (owner_id = auth.uid());

-- باقي الجداول: نفس السياسة
do $$
declare t text;
begin
  foreach t in array array[
    'store_channels','products','product_variants','inventory_movements',
    'delivery_zones','customers','conversations','messages','orders',
    'order_items','escalations','knowledge_base','offers','owner_pending_actions'
  ] loop
    execute format('alter table %I enable row level security', t);
    execute format(
      'create policy "store_isolation" on %I for all
         using (store_id = current_store_id())
         with check (store_id = current_store_id())', t);
  end loop;
end $$;
```

**تذكير:** الـ Service Role key بيتجاوز RLS. ما تحطيه أبداً بالـ Frontend، وبس بالـ backend.

## 7. Views للـ Dashboard

```sql
create view daily_sales with (security_invoker = true) as
select store_id,
       created_at::date as day,
       count(*)         as orders_count,
       sum(total)       as revenue
  from orders
 where status <> 'cancelled'
 group by store_id, created_at::date;

create view low_stock_variants with (security_invoker = true) as
select v.id as variant_id, v.store_id, p.name as product_name, v.name as variant_name,
       v.stock_quantity, v.low_stock_threshold
  from product_variants v
  join products p on p.id = v.product_id
 where v.is_active and p.is_active
   and v.stock_quantity <= v.low_stock_threshold;

create view product_stock with (security_invoker = true) as
select p.id as product_id, p.store_id, p.name,
       count(v.id)            as variants_count,
       sum(v.stock_quantity)  as total_stock
  from products p
  join product_variants v on v.product_id = p.id and v.is_active
 group by p.id, p.store_id, p.name;
```

**التنفيذ (3.1، migration 0014):** `daily_sales` بيحسب اليوم بتوقيت المتجر (`created_at at time zone stores.timezone`) مش UTC، متل صفحة الطلبات. `GET /api/reports/sales?days=` بيعبّي الأيام الفاضية بصفر وبيرجّع أكتر 5 منتجات.

**التنفيذ (3.2، migration 0015):** `low_stock_variants` (مع `product_id` زيادة) و `product_stock` (بس المنتجات المفعّلة، والـ join على `store_id` كمان). `low_stock_threshold` بيتعدّل من `PATCH /api/variants/{id}` وما بيسجّل حركة مخزون. صفحة "المخزون" وعدّاد بالقائمة.

`security_invoker = true` عشان الـ view يحترم RLS (بدونه ممكن يعرض بيانات كل المتاجر). الخاصية متوفرة بنسخ PostgreSQL الحديثة، فتأكدي من نسخة Supabase عندك.

## 8. قرارات تصميم (للنقاش)

- **ما في جدول `inventory` منفصل:** الكمية الحالية بـ `product_variants.stock_quantity`، والتاريخ بـ `inventory_movements`. أبسط، وبيكفي للتنبؤ.
- **كل منتج إله variant واحد على الأقل**، عشان مسار واحد بالكود للمخزون والطلبات.
- **الخيارات بـ `jsonb`** (`{"color":..., "size":...}`) بدل أعمدة ثابتة، عشان متجر يستخدم "اللون" ومتجر تاني "الحجم" أو "النكهة".
- **`customers` لكل متجر ولكل قناة:** نفس الشخص ممكن يراسل من قناتين، وبنعتبرهم عميلين بالبداية.
- **توحيد النص العربي (`search_text`)** بيتم بالكود قبل التخزين: توحيد الألف والهمزات، التاء المربوطة والهاء، إزالة التشكيل.
- **بيانات حساسة:** لا تخزني Bot Tokens أو WhatsApp tokens كنص عادي. بنشفّرها بـ Fernet (مكتبة `cryptography`) بالـ backend قبل الحفظ.
- **`escalations.reason` و `rule`:** `reason` تصنيف عام (enum)، و`rule` السبب التفصيلي للقاعدة الي اشتغلت، عشان الـ eval والتقارير.

## 9. ترتيب التنفيذ المقترح

1. `stores` + RLS + Auth.
2. `products` + `product_variants` + `inventory_movements` + رفع Excel.
3. `customers` + `conversations` + `messages` + `store_channels`.
4. `orders` + `order_items` + دالة خصم المخزون.
5. `delivery_zones`.
6. `escalations` + `knowledge_base` (المرحلة 2).
7. Views للـ Dashboard (المرحلة 3).
