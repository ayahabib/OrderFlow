-- One bot / WhatsApp number can be active for one store only (task 5.4).
-- Partial: a disconnected row keeps its external_id, so another store may connect it later.
create unique index store_channels_active_external_idx on store_channels (channel, external_id) where is_active;
