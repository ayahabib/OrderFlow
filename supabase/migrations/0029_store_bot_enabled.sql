-- Store-wide auto-reply switch (task 5.5). When false the bot answers no conversation of the
-- store: messages are still stored and the owner is alerted and replies by hand.
alter table stores add column bot_enabled boolean not null default true;
