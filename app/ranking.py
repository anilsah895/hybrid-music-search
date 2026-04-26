import math
import time
from datetime import datetime, timezone


# =========================================================
# PART 3 — SCORING FUNCTION (HYBRID RANKING)
# =========================================================
def calculate_final_score(r):
    """
    Combine retrieval, behavioral, and freshness signals into one ranking score.

    Expected input keys in r:
    - vector_score
    - text_score
    - clicks
    - impressions
    - created_at
    """

    # -------------------------
    # Retrieval signals
    # These are produced by the hybrid retrieval stage.
    # vector_score = semantic relevance from embeddings
    # text_score   = lexical relevance from full-text search
    # -------------------------
    vector = float(r.get("vector_score", 0.0) or 0.0)
    text = float(r.get("text_score", 0.0) or 0.0)

    # Behavioral signals gathered from feedback aggregation
    clicks = int(r.get("clicks", 0) or 0)
    impressions = int(r.get("impressions", 0) or 0)

    # -------------------------
    # CTR MODEL (Bayesian smoothing)
    # Why smoothing matters:
    # - avoids tiny samples dominating (for example 1 click / 1 impression)
    # - gives cold-start items a reasonable prior instead of extreme scores
    # -------------------------
    alpha = 5
    beta = 20
    ctr = (clicks + alpha) / (impressions + alpha + beta)

    # -------------------------
    # RECENCY MODEL (temporal decay)
    # We support:
    # - datetime objects
    # - Unix timestamps (int/float)
    # - missing values
    # This makes the function resilient during migration or mixed test setups.
    # -------------------------
    created_at = r.get("created_at")

    if created_at is None:
        # Missing timestamp -> treat as older/cold data
        age_days = 365

    elif isinstance(created_at, datetime):
        # Normalize naive datetimes to UTC for safety
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        age_days = max(
            1.0,
            (datetime.now(timezone.utc) - created_at).total_seconds() / 86400.0
        )

    elif isinstance(created_at, (int, float)):
        # Backward-compatible path for old test code using Unix timestamps
        age_days = max(1.0, (time.time() - created_at) / 86400.0)

    else:
        # Unexpected type -> fall back conservatively
        age_days = 365

    # Exponential decay gives a smooth relevance drop over time
    freshness = math.exp(-age_days / 180)

    # -------------------------
    # CONFIDENCE MODEL
    # Prevents low-volume items from looking overly trustworthy.
    # More impressions = more confidence in observed CTR-like behavior.
    # -------------------------
    if impressions == 0:
        # Neutral confidence for cold-start rows
        confidence = 0.5
    else:
        # Saturates gradually as impression count grows
        confidence = min(math.log1p(impressions) / math.log1p(100), 1.0)

    # -------------------------
    # COMBINED TEMPORAL / RELIABILITY SIGNAL
    # freshness = how new the item is
    # confidence = how much we trust its observed engagement data
    # -------------------------
    recency_score = 0.7 * freshness + 0.3 * confidence

    # -------------------------
    # FINAL HYBRID SCORE
    # Primary emphasis stays on relevance:
    # - vector + text drive retrieval quality
    # - ctr and recency refine ordering
    # -------------------------
    return (
        0.45 * vector +
        0.25 * text +
        0.20 * ctr +
        0.10 * recency_score
    )


# =========================================================
# PART 5 — DIVERSITY LAYER (POST-RANKING RE-RANKER)
# =========================================================
def diversify_results(results, k=10):
    """
    Keep only one top result per conversion_group_id.
    This avoids showing multiple sibling variants from the same generation
    near the top of the result page.

    Enforces: top 5 results must contain at least 4 distinct lineages.
    If the diverse pool doesn't reach K, fills from the non-diverse pool.
    """
    seen = set()
    diverse = []        # first K unique lineages
    non_diverse = []    # duplicates that can fill gaps if needed

    # Diversity is applied after score computation, not before.
    # Highest-scoring items should be considered first.
    sorted_results = sorted(
        results,
        key=lambda x: (-x["score"], str(x.get("id")))
    )

    for r in sorted_results:
        # conversion_group_id acts as the lineage identifier
        gid = r.get("conversion_group_id")
        # If the lineage is missing, fall back to title so we still reduce duplicates
        if gid is None:
            gid = r.get("title", "").strip().lower()

        if gid in seen:
            # This is a duplicate lineage — keep it for potential fill
            non_diverse.append(r)
        else:
            # First occurrence of this lineage
            seen.add(gid)
            diverse.append(r)

    # Enforce: if we have fewer than k diverse results, fill from non-diverse
    if len(diverse) < k:
        remaining = k - len(diverse)
        diverse.extend(non_diverse[:remaining])

    return diverse[:k]


if __name__ == "__main__":
    songs = [
        {"name": "A", "age_days": 3, "clicks": 40, "impressions": 60, "hybrid_score": 0.72},
        {"name": "B", "age_days": 730, "clicks": 1000, "impressions": 5000, "hybrid_score": 0.80},
        {"name": "C", "age_days": 1, "clicks": 1, "impressions": 1, "hybrid_score": 0.75},
        {"name": "D", "age_days": 180, "clicks": 0, "impressions": 0, "hybrid_score": 0.68},
    ]

    scored = []

    print("\nPART 3 VERIFICATION")
    print("=" * 90)
    print(f"{'Song':<6}{'Age(days)':<12}{'Clicks':<10}{'Impr':<10}{'Hybrid':<10}{'Final Score':<12}")
    print("-" * 90)

    for s in songs:
        r = {
            "vector_score": s["hybrid_score"],
            "text_score": 0.0,
            "clicks": s["clicks"],
            "impressions": s["impressions"],
            "created_at": time.time() - s["age_days"] * 86400,
        }

        final_score = calculate_final_score(r)
        scored.append({
            "name": s["name"],
            "score": final_score,
            "conversion_group_id": s["name"],
        })

        print(
            f"{s['name']:<6}{s['age_days']:<12}{s['clicks']:<10}{s['impressions']:<10}"
            f"{s['hybrid_score']:<10.2f}{final_score:<12.4f}"
        )

    ranked = sorted(scored, key=lambda x: x["score"], reverse=True)

    print("\nRANK ORDER:")
    for i, row in enumerate(ranked, 1):
        print(f"{i}. {row['name']} | {row['score']:.4f}")

    print("\nDIVERSIFIED OUTPUT:")
    print(diversify_results(ranked))