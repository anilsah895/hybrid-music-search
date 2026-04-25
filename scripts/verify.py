import asyncio
import sys

# 🔥 FIX for Windows event loop crash
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.search import search
from app.ranking import calculate_final_score, diversify_results


def run(query: str):

    print("\n" + "=" * 60)
    print("QUERY:", query)
    print("=" * 60)

    results = search(query)

    scored = []

    for r in results:
        score = calculate_final_score(r)

        scored.append({
            "conversion_group_id": r.get("conversion_group_id", "NA"),
            "title": r.get("title", "unknown"),
            "score": score
        })

    ranked = diversify_results(scored)

    for i, r in enumerate(ranked[:5]):
        print(f"{i+1}. {r['title']}  | score={r['score']:.4f}")


if __name__ == "__main__":

    # TEST 1 — RECENCY vs OLD VIRAL
    run("new pop")

    # TEST 2 — LEXICAL RESCUE CASE
    run("C major female vocal")