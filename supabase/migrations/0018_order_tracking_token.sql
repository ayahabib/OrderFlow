-- Order tracking link for the customer (task 3.6, PRD item 18).
-- A random token per order (122 random bits from gen_random_uuid): the public page
-- /track/<token> shows the order's status without a login, so the token must not be guessable
-- and must not be the order id. Existing orders get one from the default.

alter table orders
  add column tracking_token text not null default replace(gen_random_uuid()::text, '-', '');

create unique index orders_tracking_token_idx on orders (tracking_token);
