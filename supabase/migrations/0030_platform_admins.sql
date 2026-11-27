-- Platform admins (task 5.2). Added by SQL only (no sign-up path):
--   insert into platform_admins (user_id) select id from auth.users where email = '<admin email>';
-- RLS on with no policies: only the backend (service role) reads it.
create table platform_admins (
  user_id     uuid primary key references auth.users(id) on delete cascade,
  created_at  timestamptz not null default now()
);
alter table platform_admins enable row level security;
