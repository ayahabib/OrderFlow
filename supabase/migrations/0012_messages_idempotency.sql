-- Idempotent webhooks (CLAUDE.md hard rule 11): a channel message is stored once per store.
-- Adapters build external_message_id so it is unique per store, e.g. "telegram:<chat_id>:<message_id>".
-- The insert uses ON CONFLICT ... DO NOTHING against this index, so retries and races are safe.

create unique index messages_external_id_uniq
  on messages (store_id, (metadata ->> 'external_message_id'))
  where sender = 'customer' and metadata ? 'external_message_id';
