import math


def calculate_final_score(r):

    vector = r.get("vector_score", 0)
    text = r.get("text_score", 0)

    clicks = r.get("clicks", 0)
    impressions = r.get("impressions", 1)

    ctr = (clicks + 1) / (impressions + 2)

    # FIXED: consistent time model
    age_days = max(1, 365 - (r.get("created_at") or 0))
    freshness = 1 / math.log(age_days + 1)

    return (
        0.45 * vector +
        0.25 * text +
        0.20 * ctr +
        0.10 * freshness
    )


def diversify_results(results, k=10):

    seen = set()
    out = []

    for r in sorted(results, key=calculate_final_score, reverse=True):
        gid = r.get("conversion_group_id")

        if gid in seen:
            continue

        seen.add(gid)
        out.append(r)

        if len(out) >= k:
            break

    return out