-- A store asks the platform admin to connect its WhatsApp number (task 5.4).
create type whatsapp_request_status as enum ('pending', 'connected', 'rejected');

create table whatsapp_requests (
  id               uuid primary key default gen_random_uuid(),
  store_id         uuid not null references stores(id) on delete cascade,
  phone            text not null,          -- E.164
  display_name     text not null,
  status           whatsapp_request_status not null default 'pending',
  phone_number_id  text,                   -- from Meta, set by the admin on connect
  reject_reason    text,
  created_at       timestamptz not null default now(),
  handled_at       timestamptz
);
-- At most one open request per store.
create unique index whatsapp_requests_one_open on whatsapp_requests (store_id) where status = 'pending';

alter table whatsapp_requests enable row level security;
create policy "store_isolation" on whatsapp_requests for all
  using (store_id = current_store_id()) with check (store_id = current_store_id());
