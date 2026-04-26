# Hybrid Music Search — MusicGPT Technical Assessment

Hybrid music search system for generative songs using:

- Vector search (`pgvector`)
- Postgres full-text search (FTS)
- A Python reranking layer (recency + engagement)
- A diversity-aware post-processing step over sibling variants
- Buffered feedback aggregation for click/impression events

This repository is my implementation for **MusicGPT Technical Assessment v2: The Search Integrity Challenge**.

---

## Architecture Overview

The system is built around a single `music_tracks` table that stores:

- A row per *audio variant* (e.g. `conversion_path_1`, `conversion_path_2`)
- A **lineage key** (`conversion_group_id`) grouping sibling variants from the same generation
- Hybrid search fields: a pgvector `embedding` and a weighted FTS `search_vector`
- Behavioral fields: `clicks`, `impressions`, `created_at`

Around that table, the core components are:

- `app/search.py` — hybrid retrieval:
  - vector similarity using pgvector
  - FTS via Postgres `tsvector`
  - reciprocal rank fusion and lineage-aware dedup in SQL
- `app/ranking.py` — reranking + diversity:
  - `calculate_final_score` combines hybrid retrieval score, CTR, recency, and confidence
  - `diversify_results` enforces lineage-level diversity
- `app/feedback.py` — buffered click/impression aggregation to avoid row-level lock contention
- `app/seed.py` — ingestion from the provided DynamoDB-style JSON dataset

---

## How to Run

### 1. Start Postgres with pgvector

```bash
docker compose up -d
```

This brings up Postgres with the `pgvector` extension enabled (see `docker-compose.yml`).

### 2. Apply Migrations

```bash
python -m alembic upgrade head
```

This creates the `music_tracks` table and associated indexes (GIN for FTS, IVFFLAT for vectors).

### 3. Seed the Database

```bash
python -m app.seed
```

This script:

- Downloads the assessment dataset from the provided S3 URL
- Unwraps DynamoDB-style `S/N/M/L/NULL` wrappers
- Writes one row per available audio variant
- Groups siblings by `conversion_group_id`
- Populates embeddings with a deterministic placeholder
- Initializes `clicks`, `impressions`, and `created_at`

You should see:

```text
✅ Seeded 96 track variants from real dataset
```

(The exact number may change if the upstream dataset changes, but it will be ≥ 30.)

### 4. Run Search Verification (Parts 1 & 2)

```bash
python -m scripts.verify
```

This runs a few representative queries (`"new pop"`, `"C major female vocal"`, `"energetic electronic"`) through the full hybrid pipeline and prints the top results with scores and lineage ids.

### 5. Run Feedback Buffer Validation (Part 4)

```bash
python -m scripts.simulate_feedback
```

This generates high-QPS feedback events into `FeedbackBuffer`, flushes the aggregated counters to Postgres, and verifies that `clicks` and `impressions` in `music_tracks` match the expected buffered totals.

---

## Files of Interest

- `alembic/` — database migrations (Alembic)
- `docker-compose.yml` — Postgres + pgvector Docker setup
- `app/models.py` — SQLAlchemy ORM models for `music_tracks`
- `app/search.py` — hybrid retrieval logic (fixed from the broken query in the prompt)
- `app/ranking.py` — reranking and diversity functions
- `app/feedback.py` — in-memory feedback buffer and flush logic
- `app/seed.py` — data ingest from `song_metadata.json` S3 URL
- `scripts/verify.py` — simple CLI to run a few search queries and inspect output
- `scripts/simulate_feedback.py` — stress-tests the feedback buffer
- `DECISIONS.md` — detailed design decisions for each part of the assessment

---

## Verification Snippets

### Part 1 & 2 — Hybrid Search Sanity Check

After seeding and running:

```bash
python -m scripts.verify
```

Sample output:

```text
============================================================
QUERY: C major female vocal
============================================================
1. Sunlit Melody       | score=0.1364 | group=44c66afc-... | ...
2. Snowy Holiday Nights| score=0.1204 | group=59c04f7b-... | ...
3. Snowy Holiday Nights| score=0.1195 | group=a9f452c1-... | ...
...
```

This shows that explicit technical intent (`C major`, vocal style) now surfaces plausible lexical matches via FTS instead of relying on vague semantic similarity from embeddings alone.

### Part 3 — Reranking Verification (A vs B vs C vs D)

The `calculate_final_score` function in `app/ranking.py` can be verified by running it directly with hardcoded test data. The expected output:

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

This demonstrates:

- A (3 days, strong CTR) beats B (2 years, historically popular).
- C (1/1 clicks) does **not** beat A despite 100% CTR, because confidence is low.
- D with 0/0 is not collapsed to zero due to cold-start handling.

### Part 4 — Concurrency Validation

After running the feedback validation:

```bash
python -m scripts.simulate_feedback
```

Expected output:

```text
================================================================================
PART 4 VALIDATION: BUFFERED FEEDBACK
================================================================================
Tracks selected: 5
Total events generated: 35000
Buffering time: 0.0232s

Buffered -> <id> | <title> | clicks=2000 impressions=5000
...

Flush time: 0.0260s

DB verification:
OK -> <id> | <title> | clicks=2000 (expected 2000) | impressions=5000 (expected 5000)
...

SUCCESS: Part 4 validation passed. Events were buffered in memory and flushed as batched counter updates.
```

This confirms that high-QPS feedback events are aggregated in memory and persisted as batched counter updates, avoiding row-level lock contention.

---

## Notes and Limitations

- Embeddings used here are deterministic placeholders to keep the seeding step self-contained; in a real deployment, both indexed songs and live queries would use the same production embedding model.
- Diversity is lineage-based: it prevents multiple variants from the same generation lineage from clustering in the top results, but it does not attempt genre/artist-level diversification.
- Feedback buffering uses an in-memory `FeedbackBuffer` that aggregates `click` and `impression` events and periodically flushes batched counter updates to `music_tracks` to reduce row-level lock contention.
- The maximum staleness of engagement counters feeding the reranker is roughly the flush interval (about 1 second), which is acceptable given the heuristic nature of the reranking logic.
- Because the buffer is in-memory, unflushed events can be lost on process restart; a production system would move this path to a durable queue or store (e.g. Redis, Kafka) to avoid data loss.

See `DECISIONS.md` for deeper reasoning and trade-offs for each part.