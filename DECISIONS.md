# DECISIONS.md

This document explains the major technical decisions I made for the MusicGPT Search Integrity challenge, the alternatives I considered, and the known limitations of the current implementation.

---

## Part 1 — Schema Design Under Ambiguity

### Decision made

I designed the schema around a single `music_tracks` table with a mix of first-class typed columns and flexible `JSONB` storage.

Promoted first-class columns:
- `id`
- `external_id`
- `title`
- `acoustic_prompt_descriptive`
- `conversion_group_id`
- `conversion_index`
- `embedding`
- `search_vector`
- `clicks`
- `impressions`
- `created_at`

Kept in `JSONB`:
- `all_tags`
- `extra_metadata`
- `raw_payload`

I used `conversion_group_id` to model the relationship between `conversion_path_1` and `conversion_path_2`. These are not independent songs; they are sibling outputs from the same generation lineage. In the current schema, `conversion_group_id` is the lineage key.

### Why

The dataset is a DynamoDB-style export with wrapper types like `{ "S": ... }`, `{ "N": ... }`, `{ "L": [...] }`, `{ "M": {...} }`, and sparse/missing fields. I did not assume uniform structure. Instead, I normalized a small set of fields that are directly useful for retrieval and ranking, and kept the rest in `JSONB` so the ingest pipeline remains robust as the export evolves.

I promoted `title` and `acoustic_prompt_descriptive` because they are central to ranking and retrieval quality. I promoted `conversion_group_id` because diversity across sibling outputs is a core business requirement in this task. I promoted `clicks`, `impressions`, and `created_at` because Part 3 requires reranking using engagement and recency.

I kept `all_tags`, `extra_metadata`, and `raw_payload` in `JSONB` because they are semi-structured and evolving. Some of this metadata is useful for debugging, backfills, or future feature work, but not every nested field deserves a permanent top-level column.

### TSVECTOR design

I built `search_vector` from weighted textual fields. The weighting principle is:
- title should rank highest,
- descriptive prompt text should rank next,
- tags should contribute recall but not dominate.

That means a direct title match should outrank a loose tag overlap. This matches user expectations for exact lexical intent like "C major" or "female vocal".

### Missing `acoustic_prompt_descriptive`

This is the richest field for embeddings, but it is not guaranteed to exist on every record. My decision was:
- use `acoustic_prompt_descriptive` when present,
- otherwise fall back to the best available descriptive text, such as `prompt`, `sounds_1`, `sounds_2`, or a concatenation of useful metadata,
- keep the fallback explicit rather than silently embedding empty text.

I chose this because dropping records with missing descriptive text would reduce recall, while embedding empty strings would create low-signal vectors and pollute retrieval quality.

### Indexing strategy

I indexed:
- primary key on `id`,
- `ivfflat` index on `embedding` for vector similarity,
- `GIN` index on `search_vector` for full-text search.

I deliberately did not create many secondary indexes on JSONB fields because the current retrieval path does not query those fields directly at runtime. I prefer to add indexes when a concrete query pattern exists rather than pre-optimizing every possible metadata access path.

### What I considered and rejected

- **Fully flattening the DynamoDB export into many columns**: rejected because the source is sparse and evolving; this would create brittle ingest code and many mostly-null columns.
- **Storing everything only as JSONB**: rejected because search-critical fields need strong typing, indexing, and predictable access.
- **Modeling each conversion path as a separate fully independent song**: rejected because the task explicitly says they are siblings from the same generation lineage.

### Known limitations

- The current schema uses `conversion_group_id` as the lineage key. The README originally referenced `generation_id`; I aligned the implementation to the actual schema instead.
- Some useful technical metadata like BPM/key can remain nested unless explicitly extracted during ingest. That is acceptable for this assessment but could be expanded in a production system.

---

## Part 2 — Broken Search, Find the Bug

### Decision made

I corrected the hybrid retrieval query in two places:
1. the FTS parsing/query construction,
2. the post-fusion duplicate handling and ranking setup.

### Bug 1 — FTS handling

The broken query used `to_tsquery('english', $2)` directly. That is too strict and too syntax-sensitive for raw user queries like:
- `C major`
- `128 BPM`
- `female vocal`

`to_tsquery` expects tsquery syntax, not arbitrary user text. In practice, this causes keyword-specific or phrase-like queries to underperform or fail to match as expected, even when the terms exist in the source text.

#### User impact

Users experience this as "exact words are in the data, but search still misses them." Vibe queries may still look okay because the vector side retrieves semantically similar songs, masking the lexical weakness. But technical or structured intent, such as key, BPM, or vocal style, gets lost.

#### Fix

I switched to `plainto_tsquery('english', :query)` in the corrected implementation. This is safer for plain user text and produces a more reliable lexical match path for technical queries.

### Bug 2 — fusion/ranking issue

The original fusion strategy used reciprocal rank fusion, which is reasonable, but it did not account for sibling variants or result-set diversity. In this dataset, multiple rows can represent closely related outputs from the same generation lineage. Without explicit lineage-aware handling, the top results get clogged by near-duplicates.

There is also a practical ranking issue in using a very flat RRF constant without downstream reranking. If all candidates sit in a narrow score range and no second-stage logic exists, the final ordering feels arbitrary.

#### User impact

Users see repeated versions of effectively the same song in the top results, which makes search feel spammy and low quality. They also see older or weakly relevant tracks dominate because the fused retrieval score alone has no notion of engagement, freshness, or diversity.

#### Fix

I kept hybrid retrieval but added:
- lineage-aware deduplication using `conversion_group_id`,
- a Python reranker for recency and engagement,
- a diversity pass to reduce sibling clustering.

### Corrected query

The corrected retrieval query uses:
- `plainto_tsquery` for FTS,
- vector and FTS candidate generation,
- reciprocal rank fusion,
- `DISTINCT ON (conversion_group_id)` to retain the best candidate per lineage before final ordering.

This makes lexical queries work better and removes obvious duplicate sibling clutter before reranking.

### What I considered and rejected

- **Using only vector search**: rejected because it performs poorly on explicit technical queries like BPM/key.
- **Using only FTS**: rejected because vibe-style natural-language intent is better captured with embeddings.
- **Relying only on SQL-level hybrid fusion with no reranking**: rejected because business constraints require freshness, confidence-aware engagement, and diversity.

### Known limitations

- The current retrieval stage still depends on embedding quality. If query embeddings are placeholder or unavailable, FTS contributes most of the quality.
- `plainto_tsquery` is safer than `to_tsquery` for user input, but it is not perfect for all music-specific phrase matching. A production system could further improve lexical handling with phrase search, synonyms, or custom dictionaries.

---

## Part 3 — Re-Ranking Design

### Decision made

I designed a second-stage scoring function that combines:
- hybrid retrieval score,
- recency,
- engagement quality,
- confidence in engagement,
- cold-start protection.

The goal is to prevent old, historically popular songs from dominating forever while still rewarding strong performance from newer songs.

### Formula shape

My reranker uses these ideas:

1. **Hybrid score remains the base relevance signal**
   - Retrieval still matters most; reranking should refine, not replace, retrieval.

2. **Recency boost with smooth decay**
   - Newer songs get an advantage, but it decays gradually rather than dropping off a cliff.
   - I prefer an exponential or half-life style curve over a hard threshold because it behaves more predictably across edge cases.

3. **Engagement modeled as quality, not raw volume**
   - I use CTR-like behavior (`clicks / impressions`) as the quality signal.
   - Raw clicks alone would unfairly favor older content that simply had more time to accumulate exposure.

4. **Confidence weighting**
   - `40/60` should be trusted more than `1/1`.
   - I use a confidence factor that increases with impression count so tiny samples do not dominate.

5. **Cold start handling**
   - Songs with zero impressions should not collapse to zero.
   - I assign them a neutral prior rather than treating them as poor performers.

### Why this shape

This shape matches the business problem described in the task:
- Song A (3 days, 40/60 CTR, hybrid 0.72) should outrank Song B (2 years, 1000/5000 CTR, hybrid 0.80).
- Song C (1 day, 1/1 CTR, hybrid 0.75) should not beat Song A because the sample is too small to be trustworthy.
- Song D (6 months, 0/0 CTR, hybrid 0.68) should still remain eligible due to cold start.

A pure recency boost would overreward new but unproven songs. A pure CTR boost would overreward tiny denominators. A pure click count would fossilize old content. This combined shape is more balanced.

### What I considered and rejected

- **Raw clicks boost**: rejected because it permanently advantages old content.
- **Pure CTR**: rejected because `1/1` would incorrectly look perfect.
- **No cold-start handling**: rejected because new songs with zero impressions would be unfairly suppressed.

### Known limitations

- The exact weights are heuristic and could be tuned with real user outcome data.
- This reranker is global, not personalized.
- I did not implement full online learning or pairwise rank optimization because that is beyond the scope of the assessment.

---

## Part 4 — Concurrency Decision

### Decision made

I chose buffered aggregation instead of directly updating the `songs`/`music_tracks` row on every feedback event.

The implementation direction is:
- accept feedback events on `POST /feedback`,
- accumulate increments in an in-memory buffer keyed by `output_id`,
- flush aggregated updates to Postgres on a short interval (for example every 1 second),
- apply batched increments in SQL.

### Why

At approximately 5,000 feedback events per second, naive row-by-row updates create row-level lock contention on hot tracks. Buffering and batching drastically reduce write amplification and lock churn.

This is the simplest committed approach that addresses the stated bottleneck without introducing a full external queueing system during the assessment.

### Maximum staleness

The maximum staleness is approximately the flush interval plus a small processing delay. With a 1-second flush cadence, counts used by the reranker may be up to about 1 second stale.

I consider that acceptable for search reranking because the reranker is already heuristic and does not require millisecond-accurate counters. A one-second lag is a reasonable tradeoff for much higher write throughput.

### Restart behavior

If the process restarts mid-flush, in-memory buffered events that were not yet persisted may be lost. That is the major failure mode of this simplified design.

I accept this for the assessment because the prompt asked me to commit to one approach and reason through failure modes. In production, I would move the buffer into Redis, Kafka, or another durable queue to reduce loss risk.

### Next bottleneck after row locks

Once row-lock contention is reduced, the next likely bottleneck is:
- database write throughput during flushes,
- application process CPU/network overhead,
- or the feedback ingestion layer itself if a single process handles all 5,000 rps.

At that point, the architecture would likely need either a durable event stream or horizontally scaled consumers.

### What I considered and rejected

- **Naive `UPDATE ... SET clicks = clicks + 1` per request**: rejected because the prompt explicitly says this causes lock contention.
- **Redis/Kafka first**: rejected for the assessment because I preferred a smaller, explainable implementation with clear tradeoffs.
- **Event loss denial**: rejected because it would be dishonest; the in-memory buffer absolutely risks loss on restart.

### Known limitations

- Buffered in-memory aggregation is not durable.
- Hot-key skew can still produce bursty flushes for extremely popular songs.
- The staleness window is deliberate, not accidental.

---

## Part 5 — Diversity in Results

### Decision made

I implemented a post-processing `diversify_results(ranked_list)` step after reranking.

The diversity rule is:
- if two results share the same lineage (`conversion_group_id`), later ones are penalized or deferred,
- the top 5 should contain at least 4 distinct lineages when enough distinct lineages are available,
- if the candidate set does not contain enough distinct lineages, the function returns the best possible mix rather than failing.

### Why

This dataset contains sibling outputs from the same generation lineage. Even if they are individually relevant, showing 4–5 near-variants in the top 10 is bad product behavior. Users want variety across distinct generations, not repeated micro-variants.

I treat this as a post-processing problem rather than a retrieval problem because:
- retrieval should maximize recall,
- diversity should shape presentation after relevance is known.

### Time complexity

The implementation is linear in the size of the ranked list if done with sets and a small number of passes: approximately `O(n)`.

If the result set is 1,000 items, this is not a practical performance problem. The retrieval query itself is much more expensive than a linear Python post-processing pass over 1,000 candidates.

### What I considered and rejected

- **Hard delete of all but one sibling during retrieval only**: rejected because I still want the reranker/diversifier to have flexibility if the result pool is small.
- **Pairwise similarity-based diversification across all features**: rejected as overkill for the assessment. The lineage key already captures the business problem cleanly.
- **No diversity pass**: rejected because the prompt explicitly identifies sibling clustering as a user-visible defect.

### Known limitations

- This approach uses lineage/group identity, not acoustic distance between tracks.
- If lineage metadata is missing or wrong, the diversity pass can only fall back to weaker heuristics.
- It does not explicitly optimize for genre or artist variety beyond lineage separation.

---

## Verification Notes

### What I verified

I verified that:
- the hybrid query runs end-to-end without async loop failures,
- duplicate sibling outputs are removed from top results using `conversion_group_id`,
- lexical queries like `"C major female vocal"` now surface plausible results through improved FTS handling,
- score spread is more interpretable after adjusting the fusion constant.

Example verification output from the repo after fixes:
- `"new pop"` returns distinct titles without duplicate sibling clutter,
- `"C major female vocal"` returns lexical matches instead of relying only on vague semantic similarity,
- `"energetic electronic"` ranks relevant electronic tracks near the top.

### Current limitations in verification

At the current stage of the repo, real query embeddings may still need to be fully wired depending on runtime environment and API key availability. When embeddings are unavailable, the system falls back to a placeholder strategy, which weakens semantic retrieval quality.

That means the architecture and retrieval pipeline are correct, but final quality still depends on using the same real embedding model for both indexed tracks and live queries.

---

## Final reflection

My overall design philosophy was:
- use SQL for high-recall retrieval and first-pass structural cleanup,
- use Python for business-aware reranking and diversity,
- keep schema decisions pragmatic under ambiguous data,
- explicitly document tradeoffs instead of pretending there is one perfect solution.

For this assessment, I prioritized:
1. correctness,
2. explainability,
3. maintainability,
4. alignment with product behavior described in the prompt.

Where I made heuristic choices, I tried to make them simple enough to defend and improve later.