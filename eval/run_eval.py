"""Extraction eval: run the real LLM on eval/extraction_testset.jsonl and report per-field accuracy.

Usage (from backend/, so the app's settings and .env are used):
    uv run python ../eval/run_eval.py              # dev split (tune prompts on this only)
    uv run python ../eval/run_eval.py --split test # final measurement; don't tune after looking
    uv run python ../eval/run_eval.py --split all

Calls the OpenAI API (costs a little). Synthetic data only. Results are saved to
eval/results/<date>_<model>_extraction_<split>.json.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR.parent / "backend"))
sys.path.insert(0, str(EVAL_DIR))

from app.config import get_settings  # noqa: E402
from app.llm.client import LLMError, OpenAILLM  # noqa: E402
from app.llm.extraction import analyze_message  # noqa: E402
from scoring import score_record, summarize  # noqa: E402

ORDER = ["intent", "item_count", "product_text", "color", "size", "quantity",
         "address_text", "phone", "wants_to_confirm", "full_message"]


def load(split: str) -> list[dict]:
    rows = [json.loads(line) for line in (EVAL_DIR / "extraction_testset.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows if split == "all" else [r for r in rows if r["split"] == split]


async def predict_all(llm, records, concurrency: int) -> list[dict | None]:
    sem = asyncio.Semaphore(concurrency)

    async def one(record):
        async with sem:
            try:
                return (await analyze_message(llm, record["message"])).model_dump()
            except LLMError as exc:
                return {"error": exc.kind}

    return await asyncio.gather(*(one(r) for r in records))


def print_report(summary: dict, failures: list[dict], llm_errors: int, n: int, model: str, split: str) -> None:
    print(f"\nExtraction eval  model={model}  split={split}  messages={n}  llm_errors={llm_errors}\n")
    print(f"{'field':<18}{'accuracy':>10}{'correct':>10}")
    for field in ORDER:
        if field in summary:
            s = summary[field]
            print(f"{field:<18}{s['accuracy'] * 100:>9.1f}%{s['correct']:>6}/{s['total']}")
    if failures:
        print("\nMisses:")
        for f in failures:
            print(f"  {f['id']}: {', '.join(f['wrong'])}")


async def main(split: str, concurrency: int) -> None:
    settings = get_settings()
    llm = OpenAILLM(settings)
    records = load(split)
    predictions = await predict_all(llm, records, concurrency)

    scores, failures, details, llm_errors = [], [], [], 0
    for record, pred in zip(records, predictions):
        if "error" in pred:
            llm_errors += 1
            score = {"intent": False}
            if record["expected"] is not None:
                score["full_message"] = False
        else:
            score = score_record(record, pred)
        scores.append(score)
        wrong = [f for f, v in score.items() if f != "full_message" and (v is False or (isinstance(v, list) and not all(v)))]
        if "error" in pred:
            wrong = [f"llm_error:{pred['error']}"]
        if wrong:
            failures.append({"id": record["id"], "wrong": wrong})
        details.append({"id": record["id"], "message": record["message"], "expected_intent": record["expected_intent"],
                        "expected": record["expected"], "predicted": pred, "score": score})

    summary = summarize(scores)
    print_report(summary, failures, llm_errors, len(records), llm.model, split)

    results_dir = EVAL_DIR / "results"
    results_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")  # seconds: runs never overwrite each other
    safe_model = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in llm.model)
    out = results_dir / f"{stamp}_{safe_model}_extraction_{split}.json"
    out.write_text(json.dumps({
        "eval": "extraction", "model": llm.model, "split": split, "run_at": stamp,
        "messages": len(records), "llm_errors": llm_errors, "summary": summary, "details": details,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {out.relative_to(EVAL_DIR.parent)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the extraction eval")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    asyncio.run(main(args.split, args.concurrency))
