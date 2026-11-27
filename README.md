# OrderFlow: AI order taking for small online stores

Customers message the store on WhatsApp, Telegram or a web chat. The bot reads order messages in Arabic dialects (typos included), turns them into structured orders, checks stock, answers questions from the store's own information, and hands the chat to the owner whenever it shouldn't decide alone. The owner runs everything from an Arabic (RTL) web dashboard.

> **Team setup guide (Arabic, step by step): [docs/SETUP.md](docs/SETUP.md)**

## What it does

- **Order extraction:** free-text messages → validated JSON (products, variants, quantities, address), matched against the store's catalogue with Arabic normalisation.
- **Stock and money in code only:** prices, totals, delivery fees and atomic stock deduction are computed in SQL/Python, never by the LLM.
- **Forced hand-over:** complaints, discounts, large orders, allergy questions, missing data or an LLM failure always go to the owner, with a Telegram alert.
- **Store knowledge (RAG):** answers from the store's own info; anything not found is handed over, and the owner can save the answer for next time.
- **Dashboard:** orders, conversations inbox, customers, stock with run-out forecast, sales reports, audit log, a store-wide auto-reply switch.
- **Platform admin:** all stores, suspension, platform-wide numbers (demo data excluded), and connecting stores' WhatsApp numbers.

## Stack

FastAPI (Python 3.12, managed by uv) · React + TypeScript + Vite · PostgreSQL on Supabase (pgvector, pg_trgm, RLS) · OpenAI-compatible LLM · WhatsApp Cloud API, Telegram Bot API, web chat simulator behind one channel adapter.

## Quick start

Needs Git, [uv](https://docs.astral.sh/uv/) and Node.js 22+. Put `backend/.env` and `frontend/.env` in place first (templates: `.env.example`, `frontend/.env.example`).

```powershell
cd backend
uv sync
uv run python scripts/migrate.py --status
uv run uvicorn app.main:app --reload
```

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Tests: `cd backend && uv run pytest -m "not db"` (fast) or a single file with DB tests.

## Repository layout

| Path | What |
|---|---|
| `backend/app/` | API, channel adapters, conversation engine, LLM layer (`llm/` holds every model call) |
| `backend/tests/` | pytest suite (mock LLM; DB tests run in rolled-back transactions) |
| `frontend/src/` | owner dashboard and admin pages |
| `supabase/migrations/` | numbered SQL migrations |
| `eval/` | extraction, escalation, RAG and archive evaluations with results |
| `docs/` | PRD, architecture, database design, task list, setup guide |

Project rules for contributors and AI assistants: [CLAUDE.md](CLAUDE.md).
