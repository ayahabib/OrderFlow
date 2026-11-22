# MVP review (task 1.14), 2026-11-20

Covers phase 1 (tasks 0.1–1.13 + 1.3b offers). Model: `gpt-5.4-mini`. All numbers are on the
**synthetic** test sets in `eval/` (no real customer data).

## Evals

### Extraction (`eval/extraction_testset.jsonl`)

| split | messages | intent | full message correct | file |
|---|---|---|---|---|
| dev | 20 | 100% | 100% | `2026-11-19_180559_gpt-5.4-mini_extraction_dev.json` |
| **test (first run, held out until now)** | 20 | **100%** (20/20) | **100%** (19/19) | `2026-11-20_180919_gpt-5.4-mini_extraction_test.json` |

Test split, by field: item_count, product_text, color, size, quantity, address_text, phone and
wants_to_confirm are all 100%. No LLM errors.

### Forced escalation (`eval/escalation_testset.jsonl`, dev + test)

| measure | result |
|---|---|
| **Sensitive messages escalated (recall)** | **20/20 = 100%** (target 100%) |
| caught by keywords alone (works even if the LLM is down) | 16/20 |
| escalated with the expected rule | 20/20 |
| normal messages escalated by mistake | 0/12 |
| answerable questions still escalated until RAG (task 2.2) | 6 (price, hours, zones, stock) |

File: `2026-11-20_180931_gpt-5.4-mini_escalation_all.json`.

Caveat: these are small (40 + 32 messages) and written by us, so 100% means "no known failure",
not production accuracy. Adding freely written messages from real people would make it more honest.

## Security checklist (CLAUDE.md)

| check | result |
|---|---|
| Every query filters by `store_id` | ✅ All SQL in `backend/app` was scanned. The only lookups not keyed by store_id are on `stores` itself (id = store_id) and the channel row a webhook is addressed to, which happens after its secret is verified. |
| RLS on all tables | ✅ All 14 tables (migrations 0009, 0010); `test_rls_enabled_on_every_public_table` guards it. |
| No secrets in the repo | ✅ `backend/.env` and `frontend/.env` are git-ignored; no keys or tokens in tracked files or in history. The service-role key is backend only. Channel bot tokens are Fernet-encrypted in `store_channels`. |
| Webhook verification | ✅ Telegram: a per-channel secret header (HMAC of `TELEGRAM_WEBHOOK_SECRET`), checked before any DB access. Web chat: an HMAC session token bound to the store. WhatsApp is not built yet (2.5). |
| Idempotent webhooks | ✅ `external_message_id` is unique per store; covered by tests for web and Telegram. |
| Inputs validated | ✅ Upload is limited to 2 MB / 5000 rows; order quantity > 0 in code and a DB check; stock ≥ 0 by a DB check; length limits on chat text, owner replies and names; `extra="forbid"` rejects `store_id` in request bodies. |
| LLM can't change data without confirmation | ✅ The LLM only extracts. Orders need the customer's confirm button, and prices, fees and stock are computed in code/SQL. Owner chat tools (3.3) are not built yet. |

### Open findings (not fixed in 1.14)

1. **Medium, before any public deploy:** the web chat simulator endpoints are public by design.
   Anyone who knows a store's id can open sessions and send messages, and each message may cost
   an LLM call. There is no rate limiting yet. Add per-IP/per-store limits (or put the simulator
   behind owner login) before deploying.
2. **Low:** owner Telegram linking reads the notify bot's recent updates (`getUpdates`, at most 100
   within 24 h). Many stores linking within the same day could push a code out. That's fine for
   the MVP; a webhook on the notify bot would remove the limit.
3. **Info:** the Telegram customer channel is covered by tests with a fake Telegram API, but has
   **not yet been tried with a real bot over ngrok**.

## Test suite

**The full suite was not run end to end in 1.14** (the user's choice). Against Supabase in Tokyo,
every query takes about 0.57 s, so the ~205 DB tests need about 3 hours when run one after another.

What was run, each time against the code it covers:
- All non-DB tests (151) pass on the final code.
- DB tests were run per task as each one was built: 1.11 escalation + order flow + web chat (57),
  1.12 conversations (7), 1.13 Telegram + conversations (19). All passed.
- Not re-run after 1.13: the older DB suites (products, import, orders, offers, zones, RLS,
  auth). 1.11–1.13 didn't touch their code, apart from shared helpers in `tests/authutil.py`.

Follow-up: make the DB tests fast (parallel runs with pytest-xdist, or a local Postgres in Docker),
then run the whole suite once.
