-- Owner stock chat (task 3.3, ARCHITECTURE.md section 8): propose, then confirm.
-- The LLM's tool call only creates a 'pending' row. The stock changes in a separate
-- endpoint after the owner presses confirm, with an inventory_movements row (reason 'chat_edit').
-- Exactly one of change / set_to: "remove 5" stores change = -5, "make it 7" stores set_to = 7.
-- The result is recomputed from the current stock at confirm time and refused if negative.

create type pending_action_status as enum ('pending', 'confirmed', 'cancelled');

create table owner_pending_actions (
  id           uuid primary key default gen_random_uuid(),
  store_id     uuid not null references stores(id) on delete cascade,
  variant_id   uuid not null references product_variants(id) on delete cascade,
  change       integer check (change <> 0),
  set_to       integer check (set_to >= 0),
  status       pending_action_status not null default 'pending',
  created_at   timestamptz not null default now(),
  expires_at   timestamptz not null default now() + interval '15 minutes',
  resolved_at  timestamptz,
  constraint owner_pending_actions_one_amount check (num_nonnulls(change, set_to) = 1)
);

create index owner_pending_actions_store_idx on owner_pending_actions (store_id, status);

alter table owner_pending_actions enable row level security;
create policy "store_isolation" on owner_pending_actions for all
  using (store_id = current_store_id())
  with check (store_id = current_store_id());
