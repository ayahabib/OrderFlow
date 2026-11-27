-- Set by the platform admin (tasks 5.2, 5.3):
-- suspended_at: the store is suspended, so its bot answers nobody (messages are still stored).
-- is_demo: a test/demo store, left out of the platform numbers.
alter table stores
  add column suspended_at timestamptz,
  add column is_demo      boolean not null default false;
