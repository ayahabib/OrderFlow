-- Customers, conversations, messages (DATABASE.md 3.4)

create table customers (
  id               uuid primary key default gen_random_uuid(),
  store_id         uuid not null references stores(id) on delete cascade,
  channel          channel_type not null,
  external_id      text not null,        -- WhatsApp number or Telegram chat id
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
  bot_paused       boolean not null default false,
  bot_state        jsonb not null default '{}',
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
  metadata         jsonb not null default '{}',  -- extraction JSON, external_message_id, ...
  created_at       timestamptz not null default now()
);

create index messages_conv_time_idx on messages (conversation_id, created_at);
