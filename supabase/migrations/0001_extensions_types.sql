-- Extensions and enum types (DATABASE.md 3.1)

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
