-- Product matching (task 1.7): normalized aliases searchable with pg_trgm.
--
-- normalize_ar mirrors app/utils/arabic.py normalize_arabic (a test checks they agree):
-- drop diacritics/tatweel, unify alef forms, ta marbuta -> ha, alef maqsura -> ya,
-- hamza carriers, Arabic-Indic digits -> Latin, Arabic punctuation, lowercase, collapse spaces.

create or replace function normalize_ar(t text)
returns text language sql immutable parallel safe as $$
  select btrim(regexp_replace(lower(translate(
           regexp_replace(coalesce(t, ''), '[ؐ-ًؚ-ٰٟۖ-ۭـ]', '', 'g'),
           'أإآٱةىؤئ٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹،؛؟',
           'ااااهيوي01234567890123456789,;?')),
         '\s+', ' ', 'g'))
$$;

-- Wrapper so the generated column below has an immutable expression.
create or replace function alias_search_text(a text[])
returns text language sql immutable parallel safe as $$
  select normalize_ar(array_to_string(a, ' | '))
$$;

alter table products
  add column alias_text text generated always as (alias_search_text(aliases)) stored;

create index products_alias_trgm on products using gin (alias_text gin_trgm_ops);
