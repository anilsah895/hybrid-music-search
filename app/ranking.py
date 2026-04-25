import math
import time


def calculate_final_score(r):

    vector = r.get("vector_score", 0.0)
    text = r.get("text_score", 0.0)

    clicks = r.get("clicks", 0)
    impressions = r.get("impressions", 0)

    # -------------------------
    # We use a Bayesian prior to avoid extreme CTR values from low data.
    # This prevents 1/1 or 2/2 items from dominating ranking unfairly.
    # -------------------------
    alpha = 5
    beta = 20

    ctr = (clicks + alpha) / (impressions + alpha + beta)
    # We intentionally DO NOT apply additional CTR damping here,
    # because CTR is already stabilized using Bayesian smoothing.
    # Extra damping would double-penalize uncertainty.

    # -------------------------
    # FIX 2: safe recency model (no log explosion, bounded decay)
    # -------------------------
    created_at = r.get("created_at", None)

    if created_at is None:
        age_days = 365
    else:
        age_days = max(1, (time.time() - created_at) / 86400)
    # Exponential decay ensures:
    # - New items get advantage
    # - But do not overwhelm older strong content
    freshness = math.exp(-age_days / 180)  # smooth 6-month decay

    # -------------------------
    # Confidence (data reliability)
    # -------------------------
    # More impressions = more reliable signal
    # log scaling prevents large datasets from dominating
    if impressions == 0:
        # Cold start handling:
        # Treat unknown items as neutral instead of penalizing them
        confidence = 0.5
    else:
        confidence = min(math.log1p(impressions) / math.log1p(100), 1.0)

    # -------------------------
    # Recency + confidence combination
    # -------------------------
    # We use additive blending instead of multiplication to avoid:
    # - over-penalizing items that are only slightly weak in one dimension
    # - extreme collapse when either signal is low
    recency_score = 0.7 * freshness + 0.3 * confidence

    # -------------------------
    # FINAL SCORE BLEND
    # -------------------------
    # weights are tuned to favor retrieval relevance over noisy behavioral signals
    # (vector + text are primary relevance signals; CTR/recency refine ranking)
    return (
        0.45 * vector +
        0.25 * text +
        0.20 * ctr +
        0.10 * recency_score
    )


def diversify_results(results, k=10):

    seen = set()
    out = []

    # ensure stable ranking before diversification
    sorted_results = sorted(
        results,
        key=calculate_final_score,
        reverse=True
    )

    for r in sorted_results:
        gid = r.get("conversion_group_id")

        if gid in seen:
            continue

        seen.add(gid)
        out.append(r)

        if len(out) >= k:
            break

    return out


if __name__ == "__main__":
    songs = [
        {"name": "A", "age_days": 3, "clicks": 40, "impressions": 60, "hybrid_score": 0.72},
        {"name": "B", "age_days": 730, "clicks": 1000, "impressions": 5000, "hybrid_score": 0.80},
        {"name": "C", "age_days": 1, "clicks": 1, "impressions": 1, "hybrid_score": 0.75},
        {"name": "D", "age_days": 180, "clicks": 0, "impressions": 0, "hybrid_score": 0.68},
    ]

    for s in songs:
        print(s["name"], calculate_final_score(s))