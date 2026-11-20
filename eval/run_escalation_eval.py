"""Forced-escalation eval: does every sensitive message in eval/escalation_testset.jsonl go to the owner?

Usage (from backend/, so the app's settings and .env are used):
    uv run python ../eval/run_escalation_eval.py --split all
    uv run python ../eval/run_escalation_eval.py --offline   # keyword rules only, no API calls

Each message goes through the same message-level checks as the engine (engine/rules.decide):
keywords first, then the real LLM's sensitive_reason / question, then the quantity limits
(store defaults: 10 of one item, 20 in total). Target: recall 100% on should_escalate=true.

Messages that should NOT escalate but are answerable only from store data (prices, hours,
zones, availability) still go to the owner until RAG (task 2.2); they're reported apart
from real false positives.
Calls the OpenAI API unless --offline (costs a little). Synthetic data only.
Results: eval/results/<date>_<model>_escalation_<split>.json.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR.parent / "backend"))

from app.engine import rules  # noqa: E402

ITEM_LIMIT, TOTAL_LIMIT = 10, 20  # stores.large_order_* defaults
# Questions answered from store data (prices, hours, zones, stock). "Do you deliver to X?" for an
# unregistered X is answered from the zones table too ("no, we deliver to ..."), so it's here as well.
UNTIL_RAG = {"price_from_db", "answer_from_kb", "registered_zone", "fee_from_db", "availability_from_db", "unknown_zone"}


def load(split: str) -> list[dict]:
    rows = [json.loads(line) for line in (EVAL_DIR / "escalation_testset.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows if split == "all" else [r for r in rows if r["split"] == split]


async def decide_all(records: list[dict], offline: bool, concurrency: int) -> tuple[list[dict], str]:
    if offline:
        return [{"rule": rules.keyword_rule(r["message"]), "by": "keyword"} for r in records], "keywords-only"

    from app.config import get_settings
    from app.llm.client import LLMError, OpenAILLM
    from app.llm.extraction import analyze_message

    llm = OpenAILLM(get_settings())
    sem = asyncio.Semaphore(concurrency)

    async def one(record):
        keyword = rules.keyword_rule(record["message"])
        if keyword:
            return {"rule": keyword, "by": "keyword"}  # the engine never calls the LLM here
        async with sem:
            try:
                analysis = await analyze_message(llm, record["message"])
            except LLMError as exc:
                return {"rule": "llm_failure", "by": f"llm_error:{exc.kind}"}
        return {"rule": rules.decide(record["message"], analysis, ITEM_LIMIT, TOTAL_LIMIT), "by": "llm",
                "analysis": analysis.model_dump()}

    return list(await asyncio.gather(*(one(r) for r in records))), llm.model


def report(records: list[dict], decisions: list[dict], model: str, split: str) -> dict:
    sensitive = [(r, d) for r, d in zip(records, decisions) if r["should_escalate"]]
    normal = [(r, d) for r, d in zip(records, decisions) if not r["should_escalate"]]
    caught = [(r, d) for r, d in sensitive if d["rule"]]
    missed = [(r, d) for r, d in sensitive if not d["rule"]]
    by_keyword = sum(1 for _, d in caught if d["by"] == "keyword")
    reason_ok = sum(1 for r, d in caught if d["rule"] == r["reason"])
    until_rag = [(r, d) for r, d in normal if d["rule"] and set(r["tags"]) & UNTIL_RAG]
    false_pos = [(r, d) for r, d in normal if d["rule"] and not set(r["tags"]) & UNTIL_RAG]

    recall = len(caught) / len(sensitive) if sensitive else 1.0
    print(f"\nEscalation eval  model={model}  split={split}  messages={len(records)}\n")
    print(f"Sensitive messages escalated (recall): {len(caught)}/{len(sensitive)} = {recall * 100:.1f}%  (target 100%)")
    print(f"  caught by keywords (no LLM):         {by_keyword}/{len(caught)}")
    print(f"  with the expected rule:              {reason_ok}/{len(caught)}")
    print(f"Normal messages escalated (false pos): {len(false_pos)}/{len(normal)}")
    print(f"Answerable from store data, escalated until RAG (2.2): {len(until_rag)}")
    for title, rows in (("MISSED", missed), ("FALSE POSITIVE", false_pos), ("UNTIL RAG", until_rag)):
        for r, d in rows:
            print(f"  {title}: {r['id']} expected={r['reason']} got={d['rule']} ({d['by']})  {r['message']}")
    for r, d in caught:
        if d["rule"] != r["reason"]:
            print(f"  other rule: {r['id']} expected={r['reason']} got={d['rule']} ({d['by']})")

    return {"recall": recall, "sensitive": len(sensitive), "caught": len(caught), "caught_by_keyword": by_keyword,
            "reason_match": reason_ok, "normal": len(normal), "false_positives": len(false_pos),
            "until_rag": len(until_rag)}


async def main(split: str, offline: bool, concurrency: int) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    records = load(split)
    decisions, model = await decide_all(records, offline, concurrency)
    summary = report(records, decisions, model, split)
    if offline:
        return  # quick check only; saved results are for real runs

    results_dir = EVAL_DIR / "results"
    results_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    safe_model = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in model)
    out = results_dir / f"{stamp}_{safe_model}_escalation_{split}.json"
    details = [{"id": r["id"], "message": r["message"], "should_escalate": r["should_escalate"],
                "expected_rule": r["reason"], **d} for r, d in zip(records, decisions)]
    out.write_text(json.dumps({"eval": "escalation", "model": model, "split": split, "run_at": stamp,
                               "summary": summary, "details": details}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {out.relative_to(EVAL_DIR.parent)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the forced-escalation eval")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="all")
    parser.add_argument("--offline", action="store_true", help="keyword rules only, no OpenAI calls")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    asyncio.run(main(args.split, args.offline, args.concurrency))
