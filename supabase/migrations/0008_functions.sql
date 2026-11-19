-- Helper functions (DATABASE.md 5.1, 5.3, 6)

-- Per-store sequential order number. Row lock on stores serializes concurrent callers.
create or replace function next_order_number(p_store_id uuid)
returns integer language sql as $$
  update stores
     set next_order_number = next_order_number + 1
   where id = p_store_id
  returning next_order_number - 1;
$$;

-- Semantic search in the store's knowledge base.
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

-- Store of the logged-in user (used by RLS policies).
-- security definer so it can read stores without recursing into stores' own RLS.
create or replace function current_store_id()
returns uuid language sql stable security definer set search_path = public as $$
  select id from stores where owner_id = auth.uid()
$$;
