# Archive eval (task 3.7), 2026-11-23

Bot vs. staff on 22 **synthetic** "archive" chats (`eval/archive_testset.json`), model `gpt-5.4-mini`,
embeddings `text-embedding-3-small`. Raw results: `2026-11-23_151842_gpt-5.4-mini_archive.json`.

## Method

- Each chat is a real-looking exchange: the customer's messages and how a staff member replied.
- Every customer message is replayed through the real bot (`handler.ingest` + `handler.respond`, the
  web-chat path), with the real model, against a temporary store built from the sample files
  (`sample_inventory.csv`, the 4 zones in `sample_store.json`, the 6 store-info entries in
  `knowledge_testset.json`) inside one database transaction that is rolled back. No real store is touched.
- For each staff reply we wrote down **what it did** and **the key facts**. A bot turn passes when it
  does the same thing and includes the same facts (fixed checks, no AI judge; owner's choice):
  - action, read from engine signals, not wording: `ask` (asks for missing info), `summary` (total shown,
    waiting for confirm), `order_placed`, `answer`, `handoff` (to the owner);
  - `include`: one alternative from each group must be in the reply (totals, fees, options, facts);
  - `exclude`: must not be in the reply (used where the staff invented a fee).
- Where the staff broke a hard rule (arc-13 gave a discount on their own, arc-19 invented a delivery
  fee for an area the store doesn't serve), the expected action follows the rule, not the staff.
- After a handoff the bot stops replying, so the rest of that chat is skipped (as in the app).

## Results

| measure | result |
|---|---|
| turns scored | 38 (6 skipped after a handoff) |
| **turn pass** (same action + same facts) | **89.5%** (34/38) |
| same action as staff | 89.5% |
| same key facts | 92.1% |
| **must-handoff turns handed to the owner** | **100%** (6/6: complaint, discount, allergy, human, large order, not in data) |
| chats fully passed | 86.4% (19/22) |

By topic (chats fully passed): orders with all details in one message, step-by-step orders, partial
stock, edits before confirming, cancel, out-of-zone (incl. the staff's invented fee), order status,
every store-info question (hours, exchange, payment, location, delivery fee), every forced handoff.
The bot also did better than the staff on the two rule-breaking chats: no discount promised, no fee invented.

## Failures (4 turns, 2 causes)

### 1. "عندكم X؟" goes to the owner instead of being answered from the catalog (arc-03#1, arc-18#1)

| customer | staff | bot |
|---|---|---|
| عندكم جينز؟ بدي واحد | lists price, colors and sizes | "بتأكدلك من هالشي وبرجعلك" (handoff) |
| عندكم ساعات يد؟ | "ما عنا ساعات" | same handoff |

Cause: the extraction model returns `intent: question` with **no items** for availability questions, so
`engine/qa.answer` never looks at the products; the store-info search finds nothing and the rule
"not in store data" hands it over. Safe (nothing invented) but the owner gets questions the catalog
answers. Fix idea: extract the product words for questions too (prompt + test set examples), and/or a
code fallback that matches the question text against the catalog before the knowledge search.

### 2. After "that option is sold out", the bot forgets the product (arc-04#2, #3)

The customer asked for black jeans 32 (stock 0). The bot said it's sold out and dropped the item, so the
reply "خلص بدي الأزرق 32" had no product to attach to: "هاد المنتج مش موجود عنا", then the next message
got the welcome text. The staff offered the options still in stock and carried on.
Fix idea: when the asked variant is sold out, keep the product and ask which in-stock option instead
(a pending variant question listing what's available), like the staff did.

## Notes

- Synthetic chats written for this task; the bot's wording isn't scored, only actions and facts.
- The cases were written before the run and not tuned afterwards. Fixing the two causes above would
  need re-running this eval and the extraction eval (prompt change).
