import math
import time

# =========================================================
# PART 3 — SCORING FUNCTION (HYBRID RANKING)
# =========================================================
def calculate_final_score(r):

    # -------------------------
    # Retrieval signals (semantic + lexical relevance)
    # These come from Part 1/2 hybrid search stage
    # -------------------------
    vector = r.get("vector_score", 0.0)  # semantic similarity (embeddings)
    text = r.get("text_score", 0.0)      # lexical match (FTS relevance)

    clicks = r.get("clicks", 0)
    impressions = r.get("impressions", 0)

    # -------------------------
    # CTR MODEL (Bayesian smoothing)
    # Purpose:
    # - prevents small-sample bias (e.g. 1/1 CTR dominating ranking)
    # - stabilizes engagement signal under sparse data
    # -------------------------
    alpha = 5
    beta = 20

    ctr = (clicks + alpha) / (impressions + alpha + beta)

    # -------------------------
    # RECENCY MODEL (temporal decay)
    # Purpose:
    # - boosts newer content for freshness-sensitive queries
    # - avoids completely suppressing older high-quality content
    # -------------------------
    created_at = r.get("created_at", None)

    if created_at is None:
        age_days = 365  # fallback for missing timestamps (cold data ingestion cases)
    else:
        age_days = max(1, (time.time() - created_at) / 86400)

    # exponential decay gives smooth long-term relevance drop
    freshness = math.exp(-age_days / 180)

    # -------------------------
    # CONFIDENCE MODEL (data reliability)
    # Purpose:
    # - ensures low-interaction items are not overtrusted
    # - avoids 0/0 or 1/1 cases behaving like strong signals
    # -------------------------
    if impressions == 0:
        confidence = 0.5  # neutral prior for cold start items
    else:
        confidence = min(math.log1p(impressions) / math.log1p(100), 1.0)

    # -------------------------
    # COMBINED TEMPORAL SIGNAL
    # We combine:
    # - freshness (recency importance)
    # - confidence (signal reliability)
    # -------------------------
    recency_score = 0.7 * freshness + 0.3 * confidence

    # -------------------------
    # FINAL HYBRID SCORE (Part 3 output)
    # Design intent:
    # - vector/text = primary relevance (retrieval layer)
    # - ctr/recency = behavioral + temporal refinement
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

    seen = set()
    out = []

    # IMPORTANT:
    # This assumes score is already computed (Part 3 output)
    # Diversity operates ONLY on ranked list, not raw signals
    sorted_results = sorted(
        results,
        key=lambda x: x["score"],
        reverse=True
    )

    for r in sorted_results:

        # conversion_group_id = lineage identifier
        # ensures we avoid showing multiple variants of same generation
        gid = r.get("conversion_group_id")

        # skip duplicates from same lineage (diversity enforcement)
        if gid in seen:
            continue

        seen.add(gid)
        out.append(r)

        # stop once we reach top-k diversified results
        if len(out) >= k:
            break

    return out


# =========================================================
# TEST PIPELINE (SIMULATING PRODUCTION FLOW)
# =========================================================
if __name__ == "__main__":

    songs = [
        {"name": "A", "age_days": 3, "clicks": 40, "impressions": 60, "vector_score": 0.72},
        {"name": "B", "age_days": 730, "clicks": 1000, "impressions": 5000, "vector_score": 0.80},
        {"name": "C", "age_days": 1, "clicks": 1, "impressions": 1, "vector_score": 0.75},
        {"name": "D", "age_days": 180, "clicks": 0, "impressions": 0, "vector_score": 0.68},
    ]

    # -----------------------------------------------------
    # STEP 1 — Compute HYBRID SCORE (Part 3)
    # -----------------------------------------------------
    scored_results = []

    for s in songs:

        # simulate ingestion of ranking features
        r = {
            "vector_score": s["vector_score"],
            "text_score": 0.2 if s["name"] == "A" else 0.05,
            "clicks": s["clicks"],
            "impressions": s["impressions"],
            "created_at": time.time() - s["age_days"] * 86400
        }

        score = calculate_final_score(r)

        print(s["name"], score)

        scored_results.append({
            "conversion_group_id": s["name"],
            "score": score
        })

    # -----------------------------------------------------
    # STEP 2 — APPLY DIVERSITY LAYER (Part 5)
    # -----------------------------------------------------
    print("\nDIVERSIFIED OUTPUT:")

    print(diversify_results(scored_results))