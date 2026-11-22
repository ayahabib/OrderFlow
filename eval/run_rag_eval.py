"""RAG eval (task 2.2 done-when): answerable questions get correct answers, the rest go to the owner.

Usage (from backend/):  uv run python ../eval/run_rag_eval.py

Real OpenAI calls (embeddings + one chat call per question that passes retrieval; costs little).
Same decision as engine/qa.py: top-3 by cosine, MIN_SIMILARITY gate, LLM answer from the
passages only, and the number check (`grounded`). Retrieval runs in memory, no database.
Results: eval/results/<date>_<model>_rag.json
"""

import asyncio
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR.parent / "backend"))

from app.config import get_settings  # noqa: E402
from app.engine.qa import MIN_SIMILARITY, TOP_K, grounded  # noqa: E402
from app.llm.client import LLMError, OpenAILLM  # noqa: E402
from app.llm.embeddings import OpenAIEmbedder  # noqa: E402
from app.llm.qa import answer_from_passages  # noqa: E402
from app.services.knowledge import embedding_text  # noqa: E402
from app.utils.arabic import normalize_arabic  # noqa: E402


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


async def run_one(llm, embedder, entries, entry_vecs, question):
    [qv] = await embedder.embed([question])
    ranked = sorted(((cosine(qv, ev), e) for e, ev in zip(entries, entry_vecs)), key=lambda x: -x[0])[:TOP_K]
    best = ranked[0][0]
    if best < MIN_SIMILARITY:
        return {"outcome": "escalated", "why": f"similarity {best:.2f} < {MIN_SIMILARITY}", "best": best}
    passages = [f"{e['question']}\n{e['answer']}" for _, e in ranked]
    try:
        result = await answer_from_passages(llm, question, passages)
    except LLMError as exc:
        return {"outcome": "escalated", "why": f"llm_error:{exc.kind}", "best": best}
    if not result.found:
        return {"outcome": "escalated", "why": "NOT_FOUND", "best": best}
    if not grounded(result.answer, passages):
        return {"outcome": "escalated", "why": "number not in passages", "best": best, "answer": result.answer}
    return {"outcome": "answered", "answer": result.answer, "best": best}


def contains_all(answer: str, must_include: list[list[str]]) -> bool:
    text = normalize_arabic(answer)
    return all(any(normalize_arabic(alt) in text for alt in group) for group in must_include)


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    kb = json.loads((EVAL_DIR / "knowledge_testset.json").read_text(encoding="utf-8"))["entries"]
    data = json.loads((EVAL_DIR / "rag_testset.json").read_text(encoding="utf-8"))
    settings = get_settings()
    llm, embedder = OpenAILLM(settings), OpenAIEmbedder(settings)
    entry_vecs = await embedder.embed([embedding_text(e["question"], e["answer"]) for e in kb])

    rows = [(r, "answerable") for r in data["answerable"]] + [(r, "unanswerable") for r in data["unanswerable"]]
    results = await asyncio.gather(*(run_one(llm, embedder, kb, entry_vecs, r["question"]) for r, _ in rows))

    details, correct_answers, escalated_ok = [], 0, 0
    for (r, kind), res in zip(rows, results):
        if kind == "answerable":
            ok = res["outcome"] == "answered" and contains_all(res["answer"], r["must_include"])
            correct_answers += ok
        else:
            ok = res["outcome"] == "escalated"
            escalated_ok += ok
        details.append({"id": r["id"], "kind": kind, "question": r["question"], "ok": ok, **res})

    n_a, n_u = len(data["answerable"]), len(data["unanswerable"])
    print(f"\nRAG eval  chat={llm.model}  embeddings={embedder.model}  threshold={MIN_SIMILARITY}\n")
    print(f"Answerable answered correctly:   {correct_answers}/{n_a} = {correct_answers / n_a * 100:.1f}%")
    print(f"Unanswerable handed to owner:    {escalated_ok}/{n_u} = {escalated_ok / n_u * 100:.1f}%  (must be 100%)\n")
    for d in details:
        mark = "✓" if d["ok"] else "✗"
        said = d.get("answer") or d.get("why")
        print(f"  {mark} [{d['kind'][:6]}] sim={d['best']:.2f} {d['question']}  ->  {said}")

    out_dir = EVAL_DIR / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in llm.model)
    out = out_dir / f"{stamp}_{safe}_rag.json"
    out.write_text(json.dumps({"eval": "rag", "model": llm.model, "embed_model": embedder.model,
                               "threshold": MIN_SIMILARITY, "answered_correctly": correct_answers / n_a,
                               "escalated_unanswerable": escalated_ok / n_u, "details": details},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {out.relative_to(EVAL_DIR.parent)}")


if __name__ == "__main__":
    asyncio.run(main())
