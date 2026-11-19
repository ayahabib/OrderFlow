-- Stores and channels (DATABASE.md 3.2)

create table stores (
  id                      uuid primary key default gen_random_uuid(),
  owner_id                uuid not null unique references auth.users(id) on delete cascade, -- one user per store
  name                    text not null,
  phone                   text,
  currency                text not null default 'ILS',
  timezone                text not null default 'Asia/Hebron',
  owner_telegram_chat_id  bigint,                 -- owner notifications
  next_order_number       integer not null default 1,
  -- "large order" limits: forced escalation if any one is reached
  large_order_item_qty    integer not null default 10   check (large_order_item_qty > 0),
  large_order_total_qty   integer not null default 20   check (large_order_total_qty > 0),
  large_order_amount      numeric(10,2) not null default 1000 check (large_order_amount > 0),
  created_at              timestamptz not null default now()
);

create table store_channels (
  id            uuid primary key default gen_random_uuid(),
  store_id      uuid not null references stores(id) on delete cascade,
  channel       channel_type not null,
  external_id   text,           -- WhatsApp phone_number_id or Telegram bot id
  credentials   text,           -- Fernet-encrypted by the backend, never plain text
  is_active     boolean not null default true,
  created_at    timestamptz not null default now(),
  unique (store_id, channel)
);
