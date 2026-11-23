"""Archive eval (task 3.7, PRD item 19): replay synthetic staff-handled chats through the bot.

Usage (from backend/):  uv run python ../eval/run_archive_eval.py

Each customer message goes through the real engine (handler.ingest + handler.respond, the same
path as a web chat), with the real OpenAI model and embeddings, against a temporary store built
from the sample files inside ONE database transaction that is rolled back at the end: nothing is
left in the database and no real store is touched. Costs a little (one or two model calls per
message, plus embeddings for the store info).

A bot turn passes when it takes the same action as the staff reply and includes the same key
facts (eval/scoring.py: bot_action, score_turn). Once the bot hands a chat to the owner it stops
replying, so the rest of that chat is skipped, as in the real app.
Results: eval/results/<date>_<model>_archive.json
"""

import asyncio
import json
import os
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR.parent / "backend"))

import asyncpg  # noqa: E402

from app.channels.base import InboundMessage, OutboundMessage  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.engine import handler  # noqa: E402
from app.llm.client import OpenAILLM  # noqa: E402
from app.llm.embeddings import OpenAIEmbedder  # noqa: E402
from app.services import knowledge  # noqa: E402
from app.services.excel_import import plan_from_file  # noqa: E402
from app.services.inventory import apply_import  # noqa: E402
from scoring import bot_action, score_turn  # noqa: E402

DASHBOARD = "http://localhost:5173"


class CaptureAdapter:
    """Stands in for the web chat channel: keeps the bot's reply instead of sending it."""

    channel = "web"

    def __init__(self):
        self.last: OutboundMessage | None = None

    async def send(self, to: str, message: OutboundMessage) -> None:
        self.last = message


async def build_store(conn, embedder) -> uuid.UUID:
    owner = uuid.uuid4()
    await conn.execute("insert into auth.users (id, email, aud, role) values ($1, $2, 'authenticated', 'authenticated')",
                       owner, f"eval-{owner.hex[:8]}@example.test")
    store = await conn.fetchval("insert into stores (owner_id, name) values ($1, 'متجر تجريبي') returning id", owner)
    await apply_import(conn, store, plan_from_file("sample_inventory.csv", (EVAL_DIR / "sample_inventory.csv").read_bytes()))
    for z in json.loads((EVAL_DIR / "sample_store.json").read_text(encoding="utf-8"))["delivery_zones"]:
        await conn.execute("insert into delivery_zones (store_id, name, fee) values ($1, $2, $3)", store, z["name"], z["fee"])
    entries = json.loads((EVAL_DIR / "knowledge_testset.json").read_text(encoding="utf-8"))["entries"]
    await knowledge.add(conn, embedder, store, "manual", [(e["question"], e["answer"]) for e in entries])
    return store


async def counts(conn, store, conversation_id):
    return (await conn.fetchval("select count(*) from escalations where store_id = $1 and conversation_id = $2", store, conversation_id),
            await conn.fetchval("select count(*) from orders where store_id = $1 and conversation_id = $2", store, conversation_id))


async def run_conversation(conn, store, llm, embedder, conv) -> list[dict]:
    customer = f"eval-{conv['id']}-{uuid.uuid4().hex[:6]}"
    results, stopped = [], False
    for n, turn in enumerate(conv["turns"], 1):
        base = {"conversation": conv["id"], "turn": n, "customer": turn["customer"], "staff": turn["staff"],
                "expected": turn.get("any_of") or [turn["action"]], "note": turn.get("note")}
        if stopped:
            results.append({**base, "skipped": "handed to the owner earlier"})
            continue
        msg = InboundMessage(store_id=store, channel="web", external_customer_id=customer, text=turn["customer"],
                             external_message_id=f"eval:{customer}:{n}")
        ing = await handler.ingest(conn, msg)
        before = await counts(conn, store, ing.conversation_id)
        adapter = CaptureAdapter()
        started = time.perf_counter()
        await handler.respond(conn, store, ing.conversation_id, customer, turn["customer"], adapter, llm,
                              dashboard_url=DASHBOARD, embedder=embedder)
        after = await counts(conn, store, ing.conversation_id)
        raw = await conn.fetchval("select bot_state from conversations where id = $1 and store_id = $2", ing.conversation_id, store)
        state = json.loads(raw) if isinstance(raw, str) else (raw or {})
        action = bot_action(escalated=after[0] > before[0], order_placed=after[1] > before[1],
                            mode=state.get("mode", "idle"), pending=bool(state.get("pending")),
                            has_draft=bool(state.get("items") or state.get("queue")))
        reply = adapter.last.text if adapter.last else ""
        results.append({**base, "bot": reply, "bot_action": action, "ms": round((time.perf_counter() - started) * 1000),
                        **score_turn(turn, action, reply)})
        stopped = action == "handoff"
    return results


def summarize(convs, turns) -> dict:
    scored = [t for t in turns if "skipped" not in t]
    by_conv = defaultdict(list)
    for t in scored:
        by_conv[t["conversation"]].append(t["passed"])
    by_tag = defaultdict(lambda: [0, 0])
    for c in convs:
        ok = all(by_conv.get(c["id"], [False]))
        for tag in c["tags"]:
            by_tag[tag][0] += ok
            by_tag[tag][1] += 1
    pct = lambda a, b: round(100 * a / b, 1) if b else None  # noqa: E731
    return {
        "turns": len(scored),
        "turn_pass": pct(sum(t["passed"] for t in scored), len(scored)),
        "action_agreement": pct(sum(t["action_ok"] for t in scored), len(scored)),
        "facts_agreement": pct(sum(t["facts_ok"] for t in scored), len(scored)),
        "handoff_recall": pct(sum(t["action_ok"] for t in scored if t["expected"] == ["handoff"]),
                              sum(1 for t in scored if t["expected"] == ["handoff"])),
        "conversations": len(convs),
        "conversation_pass": pct(sum(all(v) for v in by_conv.values()), len(convs)),
        "by_tag": {tag: {"passed": a, "total": b} for tag, (a, b) in sorted(by_tag.items())},
    }


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    llm, embedder = OpenAILLM(settings), OpenAIEmbedder(settings)
    convs = json.loads((EVAL_DIR / "archive_testset.json").read_text(encoding="utf-8"))["conversations"]

    url = settings.database_url.get_secret_value()
    try:
        conn = await asyncpg.connect(url, statement_cache_size=0)
    except Exception as exc:  # never echo the URL (it holds the password)
        sys.exit(f"could not connect to the database ({type(exc).__name__})")
    turns = []
    tx = conn.transaction()
    await tx.start()
    try:
        store = await build_store(conn, embedder)
        for conv in convs:
            res = await run_conversation(conn, store, llm, embedder, conv)
            turns.extend(res)
            print(f"{conv['id']}: " + " ".join("-" if "skipped" in t else ("✓" if t["passed"] else "✗") for t in res), flush=True)
    finally:
        await tx.rollback()      # leave nothing behind
        await conn.close()

    summary = summarize(convs, turns)
    print(json.dumps({k: v for k, v in summary.items() if k != "by_tag"}, ensure_ascii=False))
    for t in turns:
        if "skipped" not in t and not t["passed"]:
            print(f"\n✗ {t['conversation']}#{t['turn']} expected {t['expected']} got {t['bot_action']}"
                  f"{' missing ' + str(t['missing']) if t['missing'] else ''}{' forbidden ' + str(t['forbidden']) if t['forbidden'] else ''}"
                  f"\n  customer: {t['customer']}\n  staff:    {t['staff']}\n  bot:      {t['bot']}")

    out_dir = EVAL_DIR / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    out = out_dir / f"{stamp}_{llm.model}_archive.json"
    out.write_text(json.dumps({"eval": "archive", "model": llm.model, "embed_model": embedder.model,
                               "summary": summary, "turns": turns}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsaved {out.relative_to(EVAL_DIR.parent)}")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    asyncio.run(main())
