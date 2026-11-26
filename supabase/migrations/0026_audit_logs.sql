-- Audit log (task 3.14): who changed what and when — the owner from the dashboard, the bot,
-- or the system. Append-only: the backend only inserts; the RLS policy lets the owner's own
-- client read their store's rows and nothing else (no update/delete policy exists).

create type audit_actor as enum ('owner', 'bot', 'system');

create table audit_logs (
  id          uuid primary key default gen_random_uuid(),
  store_id    uuid not null references stores(id) on delete cascade,
  actor       audit_actor not null,
  action      text not null,            -- e.g. 'order.status', 'variant.update', 'order.create'
  entity_id   uuid,                     -- the row it is about, when there is one
  before      jsonb,
  after       jsonb,
  created_at  timestamptz not null default now()
);

create index audit_logs_store_time_idx on audit_logs (store_id, created_at desc);

alter table audit_logs enable row level security;
create policy "store_read" on audit_logs for select using (store_id = current_store_id());
