# DECISIONS.md

This document explains the major technical decisions I made for the MusicGPT Search Integrity Challenge, what I considered and rejected, and the known limitations.

---

## Part 1 — Schema Design Under Ambiguity

### Decision

I designed the schema around a single `music_tracks` table with a mix of first-class typed columns and flexible JSONB storage.

Promoted first-class columns:

- `id` (PK)
- `external_id` (source ID + variant index)
- `title`
- `acoustic_prompt_descriptive` (or fallback embedding text)
- `conversion_group_id` (lineage key)
- `conversion_index` (sibling index: 0/1)
- `embedding` (pgvector)
- `search_vector` (TSVECTOR)
- `clicks`
- `impressions`
- `created_at`

Kept in JSONB:

- `all_tags`
- `extra_metadata` (prompt, sounds, lyrics, technical, core attributes, format, embedding source strategy)
- `raw_payload` (original record)

I used `conversion_group_id` to model the relationship between `conversion_path_1` and `conversion_path_2`. These are not independent songs; they are sibling outputs from the same generation lineage. In this schema, `conversion_group_id` is the lineage key.

The dataset is a DynamoDB-style export with wrapper types like `S`, `N`, `L`, `M`, and `NULL`, plus sparse or missing fields. I did not assume a uniform structure. Instead, I normalized a small set of fields that are directly useful for retrieval and ranking, and kept the rest in JSONB so the ingest pipeline remains robust as the export evolves.

I built `search_vector` from weighted textual fields. The weighting principle is:

- **Title** should rank highest.
- **Descriptive prompt text** should rank next.
- **Tags** should contribute recall but not dominate.

That means a direct title match should outrank a loose tag overlap, which matches user expectations for exact lexical intent such as `C major` or `female vocal`.

For embeddings, I treat `acoustic_prompt_descriptive` as the primary source. My decision was:

- Use `acoustic_prompt_descriptive` when present.
- Otherwise, fall back to a concatenation of `title`, `prompt`, `acoustic_prompt`, `all_tags`, `sounds_1`, and `sounds_2`.
- Store the strategy in metadata (`embedding_source_strategy`) instead of silently embedding an empty string.

I created:

- A primary key index on `id`
- An IVFFLAT index on `embedding`
- A GIN index on `search_vector`

### Considered and Rejected

- **Fully flattening** the DynamoDB export into many columns: Rejected because the source is sparse and evolving; this would create brittle ingest code and many mostly null columns.
- **Storing everything only as JSONB**: Rejected because search-critical fields need strong typing, indexing, and predictable access.
- **Modeling each conversion path as a separate fully independent song**: Rejected because the task explicitly says they are siblings from the same generation lineage.

### Known Limitations

- The current schema uses `conversion_group_id` as the lineage key; matching the field names in the source export.
- Some useful technical metadata, such as BPM and key, remains nested unless explicitly extracted; this is sufficient for the assessment but would likely be expanded in a production system.
- In addition, if the upstream DynamoDB export adds new nested fields, they are automatically preserved in `raw_payload` and `extra_metadata`, but they will not affect search or ranking until I explicitly promote them into first-class columns or indexed JSON paths. Records that lack both `conversion_path_1` and `conversion_path_2` do not produce any stored rows, which is a deliberate choice to skip unusable or malformed generations without breaking the ingest pipeline.

---

### Part 2 — Broken Search, Find the Bug

**Decision**

I identified two distinct bugs in the original hybrid retrieval pipeline.

**Bug 1 — FTS: `to_tsquery` on raw user text.** The original query used `to_tsquery('english', $2)` directly on unprocessed user input. `to_tsquery` expects tsquery syntax (`&`, `|`, `!` operators), so a natural-language query like "C major female vocal" would be parsed as a literal string requiring those operators, causing near-silent failure for keyword queries. In practice, this causes queries like "C major", "128 BPM", or "female vocal" to underperform or fail to match even when the terms exist in the source text. Vibe queries may appear to work because vector search retrieves semantically similar songs, masking the lexical weakness, but technical intent gets lost.

I switched to `plainto_tsquery('english', $2)` in the corrected implementation. `plainto_tsquery` tokenizes the input and ANDs terms automatically, making it safe for arbitrary user text.

**Bug 2 — Fusion: asymmetric COALESCE defaults in RRF.** The original RRF fusion used `COALESCE(v.rank, 100)` and `COALESCE(f.rank, 100)` as fallback ranks. This is not symmetric: a row absent from vector search gets rank 100, but a row absent from FTS also gets rank 100, even though the two candidate sets may return very different numbers of rows. Combined with `ROW_NUMBER()` in each CTE and arbitrary `LIMIT 100` values, this produces unstable fusion scores when one side returns fewer results. The original fusion also did not account for sibling variants or diversity, so multiple rows from the same `conversion_group_id` lineage could dominate top results.

I addressed this by:
- Increasing both candidate sets to `LIMIT 200` and using a smaller fusion constant (10 instead of 60) to reduce the impact of rank gaps.
- Adding `DISTINCT ON (conversion_group_id)` in SQL to retain the best candidate per lineage before final ordering.
- Adding a Python reranker (`calculate_final_score`) for recency, confidence-aware engagement, and a diversity pass to reduce sibling clustering.

**Considered and Rejected**

- **Using only vector search**: Rejected because it performs poorly on explicit technical queries such as BPM and key.
- **Using only FTS**: Rejected because vibe-style natural-language intent is better captured with embeddings.
- **Relying only on SQL-level hybrid fusion with no reranking**: Rejected because business constraints require freshness, confidence-aware engagement, and diversity.

**Known Limitations**

- Retrieval quality still depends on embedding quality. In a real deployment, both indexed songs and live queries would use the same production embedding model rather than the deterministic placeholders used here.
- `plainto_tsquery` is safer than `to_tsquery` for user input, but it is not perfect for all music-specific phrases. A production system could add phrase search, synonyms, or a custom dictionary for musical terms like "C major", "128 BPM", or "female vocal".
- The fusion constant (10) and the candidate set limit (200) were chosen heuristically to reduce rank asymmetry, but were not empirically tuned against a held-out query set.
- Embedding-based retrieval cannot reliably enforce hard constraints such as BPM or key, so lexical filtering/FTS must dominate structured queries like `128 BPM` or `C major` if we want to preserve search integrity for those cases.
- Given more time, I would add a lightweight query-intent layer that detects explicit technical constraints (BPM numbers, musical keys, vocal descriptors) and routes those queries to be lexical-first, while vibe-only queries lean more on the vector score. This ensures structured intent is not overwhelmed by vague semantic similarity.

---

## Part 3 — Re-Ranking Design

### Decision

I designed a second-stage scoring function, `calculate_final_score(r)`, that combines:

- Hybrid retrieval relevance (`vector_score`, `text_score`)
- Engagement quality via CTR-like behavior
- Confidence in engagement, driven by impressions
- Recency via time decay
- Cold-start protection

My reranker uses these ideas:

1. Hybrid score remains the base relevance signal; reranking refines retrieval rather than replacing it.
2. Recency uses a smooth decay, so newer songs get an advantage that fades gradually with an exponential curve and an approximately 180-day half-life.
3. Engagement is modeled as quality, not volume; I use a Beta-prior CTR, `(clicks + α) / (impressions + α + β)`, instead of raw clicks.
4. Confidence weighting grows with `log(1 + impressions)`, so `40/60` is trusted more than `1/1`.
5. Cold-start handling gives zero-impression items a neutral prior and nonzero confidence so they are not buried.

### Form and Behavior

The function is:

```python
final = (
    0.45 * vector +
    0.25 * text +
    0.20 * ctr +
    0.10 * recency_score
)
```
I chose exponential recency decay because user interest in music typically drops off quickly soon after release and then flattens, which is better modeled by an exponential curve than by a linear function. The Beta prior constants (for example, adding 5 pseudo-clicks and 20 pseudo-non-clicks) treat early CTR as a hint rather than a truth, so a 1/1 sample does not overpower a stable 40/60 sample with many impressions. The weights (0.45 vector, 0.25 text, 0.20 CTR, 0.10 recency) keep retrieval relevance as the dominant signal while allowing engagement quality and freshness to adjust rankings instead of completely overriding them.
with:

- `ctr = (clicks + 5) / (impressions + 5 + 20)`
- `freshness = exp(-age_days / 180)`
- `confidence = 0.5 if impressions == 0 else min(log1p(impressions) / log1p(100), 1.0)`
- `recency_score = 0.7 * freshness + 0.3 * confidence`

This matches the business problem:

- Song A (3 days old, 40/60 CTR, hybrid 0.72) should outrank Song B (2 years old, 1000/5000 CTR, hybrid 0.80).
- Song C (1 day old, 1/1 CTR, hybrid 0.75) should not beat Song A.
- Song D (6 months old, 0/0, hybrid 0.68) should remain eligible.

### Verification Output

Running:

```bash
python -m app.ranking
```

produces:

```text
PART 3 VERIFICATION
==========================================================================================
Song  Age(days)   Clicks   Impr      Hybrid    Final Score
------------------------------------------------------------------------------------------
A     3           40       60        0.72      0.5254
B     730         1000     5000      0.80      0.4312
C     1           1        1         0.75      0.4578
D     180         0        0         0.68      0.3868

RANK ORDER:
1. A | 0.5254
2. C | 0.4578
3. B | 0.4312
4. D | 0.3868
```

Interpretation:

- A beats B because recency and CTR outweigh B's slight hybrid advantage and much older history.
- C does not beat A because `1/1` is smoothed and low-confidence, so A's stronger CTR and larger impression base still win.
- D has a nonzero score due to recency and the neutral prior, which reflects cold-start protection.

### Considered and Rejected

- **Raw clicks boost**: Rejected because it permanently advantages old content.
- **Pure CTR**: Rejected because `1/1` looks perfect.
- **No cold-start handling**: Rejected because new songs with 0 impressions would be unfairly suppressed.

### Known Limitations

- The weights are heuristic and would need tuning on real user data.
- The reranker is global rather than personalized.
- I did not implement online learning or pairwise ranking; this is a reasonable heuristic layer for the assessment.

---

## Part 4 — Concurrency Decision

### Decision

I use an in-memory `FeedbackBuffer` to aggregate `click` and `impression` events instead of updating `music_tracks` on every `POST /feedback` request.

The buffer collects increments by `output_id`, and the application periodically calls `flush` to write batched counter updates to Postgres in a single commit.

I validated this approach with `scripts.simulate_feedback`, which generated high-QPS feedback, buffered it successfully, flushed it to the database, and verified that the stored counts matched the expected totals.

### Why

At roughly 5,000 feedback events per second, naive per-request `UPDATE` statements create row-level lock contention on hot tracks and amplify database writes.

Buffering in memory and flushing aggregated updates in batches reduces lock churn and keeps the write path simple enough.

### Maximum Staleness

The maximum staleness is approximately the flush interval plus a small processing delay. With a 1-second flush cadence, the reranker may read counts that are about 1 second old.

That is acceptable here because Part 3's reranker is heuristic and does not require millisecond-accurate engagement counters.

### Restart Behavior

If the process restarts before a buffered batch is flushed, those in-memory events are lost.

This is the main failure mode of the chosen design, and I accept it for the assessment in exchange for lower complexity.

### Next Bottleneck

After removing row-lock contention, the next likely bottlenecks are database write throughput during flushes, service CPU or network overhead, or the ingestion layer itself if a single process handles all 5,000 RPS.

In a production system, this would likely push the design toward a durable queue or horizontally scaled consumers.

---

## Part 5 — Diversity in Results

### Decision

I implemented a post-processing step, `diversify_results(ranked_list)`, after reranking. The diversity rule is:

- If two results share the same `conversion_group_id` (the same generation lineage), only the top-scoring one should appear in the top K.
- The top 5 should contain at least 4 distinct lineages when enough distinct lineages are available.
- If the candidate set does not contain enough distinct lineages, the function returns the best possible mix.

Implementation:

- Sort results by final score in descending order.
- Iterate through them while tracking seen `conversion_group_id` values.
- Keep only the first occurrence per lineage until K diversified results are collected.
- If `conversion_group_id` is missing, fall back to normalized `title` as a weaker identity signal.

This treats diversity as a post-ranking concern: retrieval maximizes recall, while diversity shapes presentation.

### Considered and Rejected

- **Hard-deleting siblings at retrieval time**: Rejected because I still want the reranker and diversifier to have flexibility when the pool is small.
- **Pairwise similarity-based diversification on all features**: Rejected as overkill for the assessment; lineage keys already capture the main business need.
- **No diversity pass**: Rejected because the prompt explicitly identifies sibling clustering as a user-visible defect.

### Time Complexity

The approach is \(O(n)\) over the ranked list after sorting, with one linear pass for the actual diversification step.

For a result set of 1,000 items, this is negligible compared with the cost of retrieval and embedding operations.

### Known Limitations

- Diversity uses lineage identity, not acoustic similarity. If lineage metadata is missing or incorrect, the diversity pass can only fall back to weaker heuristics.
- It does not explicitly optimize for genre or artist variety beyond lineage separation.

---

## Verification Notes

I verified that:

- The hybrid query runs end to end without async loop failures.
- Duplicate sibling outputs are removed from top results using `conversion_group_id`.
- Lexical queries such as `"C major female vocal"` now surface plausible results via improved FTS handling.
- Score spread is more interpretable after adjusting the fusion constant and adding reranking.
- The reranker behaves correctly on the four edge cases A-D described in Part 3.

Current limitation: embeddings are placeholder values in this assessment environment. Final production quality still depends on using the same real embedding model for both indexed tracks and live queries.

---

## Final Reflection

My overall design philosophy was:

- Use SQL for high-recall retrieval and first-pass structural cleanup.
- Use Python for business-aware reranking and diversity.
- Keep schema decisions pragmatic under ambiguous data.
- Explicitly document trade-offs instead of pretending there is one perfect solution.

For this assessment, I prioritized:

1. Correctness
2. Explainability
3. Maintainability
4. Alignment with the product behavior described in the prompt

Where I made heuristic choices, I tried to make them simple enough to defend and easy to improve later.