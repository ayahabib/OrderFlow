"""Knowledge-base search eval (task 2.1 done-when): do differently worded questions find the right info?

Usage (from backend/):  uv run python ../eval/run_kb_eval.py

Uses the real OpenAI embeddings (a few cents' fraction) on eval/knowledge_testset.json, and the
same text the app embeds (question + answer). Ranking is cosine similarity, like match_knowledge.
No database needed. Reports top-1 / top-3 hit rate and the similarity of each hit, which also
helps pick the answer threshold for task 2.2. Results: eval/results/<date>_<model>_knowledge.json.
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
from app.llm.embeddings import OpenAIEmbedder  # noqa: E402
from app.services.knowledge import embedding_text  # noqa: E402


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    data = json.loads((EVAL_DIR / "knowledge_testset.json").read_text(encoding="utf-8"))
    entries, queries = data["entries"], data["queries"]
    embedder = OpenAIEmbedder(get_settings())

    entry_vecs = await embedder.embed([embedding_text(e["question"], e["answer"]) for e in entries])
    query_vecs = await embedder.embed([q["query"] for q in queries])

    details, top1, top3 = [], 0, 0
    for q, qv in zip(queries, query_vecs):
        ranked = sorted(((cosine(qv, ev), e["id"]) for e, ev in zip(entries, entry_vecs)), reverse=True)
        ids = [i for _, i in ranked]
        top1 += ids[0] == q["expected"]
        top3 += q["expected"] in ids[:3]
        details.append({"query": q["query"], "expected": q["expected"], "ranked": [[i, round(s, 3)] for s, i in ranked[:3]]})

    n = len(queries)
    print(f"\nKnowledge search eval  model={embedder.model}  entries={len(entries)}  queries={n}\n")
    print(f"top-1 (right info first): {top1}/{n} = {top1 / n * 100:.1f}%")
    print(f"top-3:                     {top3}/{n} = {top3 / n * 100:.1f}%\n")
    for d in details:
        mark = "✓" if d["ranked"][0][0] == d["expected"] else "✗"
        first, second = d["ranked"][0], d["ranked"][1]
        print(f"  {mark} {d['query']}  ->  {first[0]} ({first[1]})  next: {second[0]} ({second[1]})")

    results = EVAL_DIR / "results"
    results.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    out = results / f"{stamp}_{embedder.model}_knowledge.json"
    out.write_text(json.dumps({"eval": "knowledge_search", "model": embedder.model, "run_at": stamp,
                               "top1": top1 / n, "top3": top3 / n, "details": details},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {out.relative_to(EVAL_DIR.parent)}")


if __name__ == "__main__":
    asyncio.run(main())
