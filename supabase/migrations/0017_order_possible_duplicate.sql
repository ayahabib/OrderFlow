-- Possible duplicate orders (task 3.5, PRD 4.10).
-- Before saving, the bot asks a customer who confirms the same items as one of their orders
-- from the last 24 hours (not cancelled). If they say it's a new order, it is saved with a
-- link to the earlier one so the owner sees a "possible duplicate" badge.

alter table orders
  add column possible_duplicate_of uuid references orders(id) on delete set null;
