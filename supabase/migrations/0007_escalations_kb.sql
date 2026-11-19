-- Escalations and knowledge base (DATABASE.md 3.6)

create table escalations (
  id               uuid primary key default gen_random_uuid(),
  store_id         uuid not null references stores(id) on delete cascade,
  conversation_id  uuid not null references conversations(id) on delete cascade,
  question         text not null,
  reason           escalation_reason not null,
  rule             text,      -- complaint, discount, large_order, allergy_ingredients,
                              -- unknown_zone, human_requested, not_in_data, order_status
  status           escalation_status not null default 'open',
  owner_answer     text,
  save_to_kb       boolean,   -- true = "save for bot", false = "private answer"
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
  embedding     vector(768),  -- must match the embedding model's output dimension
  is_active     boolean not null default true,
  created_at    timestamptz not null default now()
);

create index kb_embedding_idx on knowledge_base using hnsw (embedding vector_cosine_ops);
create index kb_store_idx on knowledge_base (store_id);
