# DECISIONS.md

This document explains the major technical decisions I made for the MusicGPT Search Integrity Challenge, what I considered and rejected, and known limitations.

---

## Part 1 — Schema Design Under Ambiguity

### Decision

I designed the schema around a single `music_tracks` table with a mix of first‑class typed columns and flexible JSONB storage. Promoted first‑class columns:

- `id` (PK)
- `external_id` (source id + variant index)
- `title`
- `acoustic_prompt_descriptive` (or fallback embedding text)
- `conversion_group_id` (lineage key)
- `conversion_index` (sibling index: 0/1)
- `embedding` (pgvector)
- `search_vector` (TSVECTOR)
- `clicks`, `impressions`
- `created_at`

Kept in JSONB:

- `all_tags`
- `extra_metadata` (prompt, sounds, lyrics, technical, core attributes, format, embedding_source_strategy)
- `raw_payload` (original record)

I used `conversion_group_id` to model the relationship between `conversion_path_1` and `conversion_path_2`. These are not independent songs; they are sibling outputs from the same generation lineage. In this schema, `conversion_group_id` is the lineage key.

The dataset is a DynamoDB‑style export with wrapper types like `S`, `N`, `L`, `M`, and `NULL`, and sparse/missing fields. I did not assume uniform structure. Instead, I normalized a small set of fields that are directly useful for retrieval and ranking and kept the rest in JSONB so the ingest pipeline remains robust as the export evolves.

I built `search_vector` from weighted textual fields. The weighting principle is:

- **Title** should rank highest.
- **Descriptive prompt text** should rank next.
- **Tags** should contribute recall but not dominate.

That means a direct title match should outrank a loose tag overlap, which matches user expectations for exact lexical intent like “C major” or “female vocal”.

For embeddings, I treat `acoustic_prompt_descriptive` as the primary source. My decision:

- Use `acoustic_prompt_descriptive` when present.
- Otherwise, fall back to a concatenation of `title`, `prompt`, `acoustic_prompt`, `all_tags`, `sounds_1`, `sounds_2`.
- Store the strategy in metadata (`embedding_source_strategy`) instead of silently embedding an empty string.

I created:

- A primary key index on `id`.
- An IVFFLAT index on `embedding` for vector similarity.
- A GIN index on `search_vector` for full‑text search.

### Considered and Rejected

- **Fully flattening** the DynamoDB export into many columns: rejected because the source is sparse and evolving; this would create brittle ingest code and many mostly‑null columns.
- **Storing everything only as JSONB**: rejected because search‑critical fields need strong typing, indexing, and predictable access.
- **Modeling each conversion path as a separate fully independent song**: rejected because the task explicitly says they are siblings from the same generation lineage.

### Known Limitations

- The current schema uses `conversion_group_id` as the lineage key; earlier drafts mentioned `generation_id` but I aligned the implementation to actual schema names.
- Some useful technical metadata like BPM and key remain nested unless explicitly extracted; that is acceptable for the assessment but could be expanded in a production system.

---

## Part 2 — Broken Search, Find the Bug

### Decision

I corrected the hybrid retrieval query in two places:

1. The FTS parsing/query construction.
2. The post‑fusion duplicate handling and ranking setup.

The broken query used `to_tsquery('english', $2)` directly on raw user text. That is too strict and syntax‑sensitive for queries like:

- `"C major"`
- `"128 BPM"`
- `"female vocal"`

`to_tsquery` expects tsquery syntax, not arbitrary user text. In practice, this causes keyword‑style or phrase‑like queries to underperform or fail to match even when the terms exist in the source text. Users experience this as “exact words are in the data, but search still misses them”. Vibe queries might look okay because vector search retrieves semantically similar songs, masking the lexical weakness, but technical intent gets lost.

I switched to `plainto_tsquery('english', $2)` in the corrected implementation. This is safer for plain user text and produces a more reliable lexical match path for technical queries.

The original fusion strategy used reciprocal rank fusion but did not account for sibling variants or diversity. Multiple rows can represent closely related outputs from the same generation lineage; without explicit lineage‑aware handling, the top results get clogged by near‑duplicates. Additionally, using a flat RRF constant without downstream reranking can produce a very narrow score range where final ordering feels arbitrary.

I kept hybrid retrieval but added:

- Lineage‑aware deduplication using `conversion_group_id`.
- A Python reranker for recency and engagement.
- A diversity pass to reduce sibling clustering.

The corrected retrieval query uses:

- `plainto_tsquery` for FTS.
- Both vector and FTS candidate generation.
- Reciprocal rank fusion.
- `DISTINCT ON (conversion_group_id)` to retain the best candidate per lineage before final ordering.

This makes lexical queries work better and removes obvious duplicate sibling clutter before reranking.

### Considered and Rejected

- **Using only vector search**: rejected because it performs poorly on explicit technical queries like BPM and key.
- **Using only FTS**: rejected because vibe‑style natural‑language intent is better captured with embeddings.
- **Relying only on SQL‑level hybrid fusion with no reranking**: rejected because business constraints require freshness, confidence‑aware engagement, and diversity.

### Known Limitations

- Retrieval quality still depends on embedding quality. If query embeddings are placeholder or unavailable, FTS contributes most of the quality.
- `plainto_tsquery` is safer than `to_tsquery` for user input, but not perfect for all music‑specific phrases; production could add phrase search, synonyms, or custom dictionaries.

---

## Part 3 — Re‑Ranking Design

### Decision

I designed a second‑stage scoring function `calculate_final_score(r)` that combines:

- Hybrid retrieval relevance (`vector_score`, `text_score`).
- Engagement quality via CTR‑like behavior.
- Confidence in engagement (impression‑driven).
- Recency via time decay.
- Cold‑start protection.

My reranker uses these ideas:

1. Hybrid score remains the base relevance signal — reranking refines retrieval; it does not replace it.
2. Recency boost with smooth decay — newer songs get an advantage that decays gradually (exponential with ~180‑day half‑life) instead of a hard cutoff.
3. Engagement modeled as quality, not volume — I use a Beta‑prior CTR `(clicks + α) / (impressions + α + β)` instead of raw clicks.
4. Confidence weighting — a confidence term grows with `log(1 + impressions)` so `40/60` is trusted more than `1/1`.
5. Cold‑start handling — items with zero impressions get a neutral prior and nonzero confidence so they are not buried.

### Form and Behavior

The function:

```python
final = (
    0.45 * vector +
    0.25 * text +
    0.20 * ctr +
    0.10 * recency_score
)
```

with:

- `ctr = (clicks + 5) / (impressions + 5 + 20)`  
- `freshness = exp(-age_days / 180)`  
- `confidence = 0.5 if impressions == 0 else min(log1p(impressions) / log1p(100), 1.0)`  
- `recency_score = 0.7 * freshness + 0.3 * confidence`

This matches the business problem:

- Song A (3 days, 40/60 CTR, hybrid 0.72) should outrank B (2 years, 1000/5000 CTR, hybrid 0.80).
- Song C (1 day, 1/1 CTR, hybrid 0.75) should not beat A.
- Song D (6 months, 0/0, hybrid 0.68) should remain eligible.

### Verification Output

Running:

```bash
python -m app.ranking
```

gives:

```text
PART 3 VERIFICATION
==========================================================================================
Song  Age(days)   Clicks    Impr      Hybrid    Final Score
------------------------------------------------------------------------------------------
A     3           40        60        0.72      0.5254
B     730         1000      5000      0.80      0.4312
C     1           1         1         0.75      0.4578
D     180         0         0         0.68      0.3868

RANK ORDER:
1. A | 0.5254
2. C | 0.4578
3. B | 0.4312
4. D | 0.3868
```

Interpretation:

- A beats B: recency + CTR outweigh B’s slight hybrid advantage and older history.
- C does not beat A: `1/1` is smoothed and low‑confidence, so A’s stronger CTR and more impressions win.
- D has a non‑zero score due to recency and neutral prior, reflecting cold‑start protection.

### Considered and Rejected

- **Raw clicks boost**: rejected because it permanently advantages old content.
- **Pure CTR**: rejected because `1/1` looks perfect.
- **No cold‑start handling**: rejected because new songs with 0 impressions would be unfairly suppressed.

### Known Limitations

- Weights are heuristic and would be tuned on real user data.
- The reranker is global, not personalized.
- I did not implement online learning or pairwise ranking; this is a reasonable heuristic layer for the assessment.

---

## Part 4 — Concurrency Decision

### Decision made

I use an in-memory `FeedbackBuffer` to aggregate `click` and `impression` events instead of updating `music_tracks` on every `POST /feedback` request.[file:174]  
The buffer collects increments by `output_id`, and the application periodically calls `flush` to write batched counter updates to Postgres in a single commit.[file:174][file:163]  
I validated this approach with `scripts.simulate_feedback`, which generated high-QPS feedback, buffered it successfully, flushed it to the database, and verified that the stored counts matched the expected totals.[file:163]

### Why

At roughly 5,000 feedback events per second, naive per-request `UPDATE` statements create row-level lock contention on hot tracks and amplify database writes.[file:163]  
Buffering in memory and flushing aggregated updates in batches reduces lock churn and keeps the write path simple enough for this assessment.[file:163]

### Maximum staleness

The maximum staleness is approximately the flush interval plus a small processing delay; with a 1-second flush cadence, the reranker may read counts that are about 1 second old.[file:163]  
That is acceptable here because Part 3’s reranker is heuristic and does not require millisecond-accurate engagement counters.[file:163]

### Restart behavior

If the process restarts before a buffered batch is flushed, those in-memory events are lost.[file:163]  
This is the main failure mode of the chosen design, and I accept it for the assessment in exchange for lower complexity.[file:163]

### Next bottleneck

After removing row-lock contention, the next likely bottlenecks are database write throughput during flushes, service CPU/network overhead, or the ingestion layer itself if a single process handles all 5,000 rps.[file:163]  
In a production system, this would likely push the design toward a durable queue or horizontally scaled consumers.[file:163]
---

## Part 5 — Diversity in Results

### Decision

I implemented a post‑processing `diversify_results(ranked_list)` step after reranking. The diversity rule:

- If two results share the same `conversion_group_id` (same generation lineage), only the top‑scoring one should appear in the top‑K.
- The top 5 should contain at least 4 distinct lineages when enough distinct lineages are available.
- If the candidate set does not contain enough distinct lineages, the function returns the best possible mix.

Implementation:

- Sort results by final score descending.
- Iterate, tracking seen `conversion_group_id`s.
- Keep only the first occurrence per lineage until K diversified results are collected.
- If `conversion_group_id` is missing, fall back to normalized `title` as a weaker identity.

This treats diversity as a post‑ranking concern: retrieval maximizes recall; diversity shapes presentation.

### Considered and Rejected

- **Hard‑deleting siblings at retrieval time**: rejected because I still want the reranker/diversifier to have flexibility when the pool is small.
- **Pairwise similarity‑based diversification on all features**: rejected as overkill for the assessment; lineage keys already capture the main business need.
- **No diversity pass**: rejected because the prompt explicitly identifies sibling clustering as a user‑visible defect.

### Time Complexity

The approach is O(n) over the ranked list (sort + one linear pass). For a result set of 1,000 items this is negligible compared to the cost of retrieval and embedding operations.

### Known Limitations

- Diversity uses lineage identity, not acoustic similarity; if lineage metadata is missing or wrong, the diversity pass can only fall back to weaker heuristics.
- It does not explicitly optimize for genre/artist variety beyond lineage separation.

---

## Verification Notes

I verified that:

- The hybrid query runs end‑to‑end without async loop failures.
- Duplicate sibling outputs are removed from top results using `conversion_group_id`.
- Lexical queries like `"C major female vocal"` now surface plausible results via improved FTS handling.
- Score spread is more interpretable after adjusting the fusion constant and adding reranking.
- The reranker behaves correctly on the four edge cases A–D described in Part 3.

Current limitation: embeddings are placeholder in this assessment environment; final quality in production still depends on using the same real embedding model for both indexed tracks and live queries.

---

## Final Reflection

My overall design philosophy was:

- Use SQL for high‑recall retrieval and first‑pass structural cleanup.
- Use Python for business‑aware reranking and diversity.
- Keep schema decisions pragmatic under ambiguous data.
- Explicitly document trade‑offs instead of pretending there is one perfect solution.

For this assessment, I prioritized:

1. Correctness.
2. Explainability.
3. Maintainability.
4. Alignment with the product behavior described in the prompt.

Where I made heuristic choices, I tried to make them simple enough to defend and easy to improve later.