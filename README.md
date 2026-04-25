# Hybrid Music Search — MusicGPT Technical Assessment

Hybrid music search system for generative songs using:

- Vector search (`pgvector`)
- Postgres full‑text search (FTS)
- A Python reranking layer (recency + engagement)
- A diversity‑aware post‑processing step over sibling variants

This repository is my implementation for **MusicGPT Technical Assessment v2: The Search Integrity Challenge**.

---

## Architecture Overview

The system is built around a single `music_tracks` table that stores:

- A row per *audio variant* (e.g. `conversion_path_1`, `conversion_path_2`)
- A **lineage key** (`conversion_group_id`) grouping sibling variants from the same generation
- Hybrid search fields: a pgvector `embedding` and a weighted FTS `search_vector`
- Behavioral fields: `clicks`, `impressions`, `created_at`

Around that table, the core components are:

- `search.py` — hybrid retrieval:
  - vector similarity using pgvector
  - FTS via Postgres `tsvector`
  - reciprocal rank fusion and lineage‑aware dedup in SQL
- `ranking.py` — reranking + diversity:
  - `calculate_final_score` combines hybrid retrieval score, CTR, recency, and confidence
  - `diversify_results` enforces lineage‑level diversity
- `feedback.py` — buffered click/impression aggregation to avoid row‑level lock contention
- `seed.py` — ingestion from the provided DynamoDB‑style JSON dataset

---

## How to Run

### 1. Start Postgres with pgvector

```bash
docker compose up -d
```

This brings up Postgres with the `pgvector` extension enabled (see `docker-compose.yml`).

### 2. Apply Migrations

```bash
poetry run alembic upgrade head
# or
python -m alembic upgrade head
```

This creates the `music_tracks` table and associated indexes (GIN for FTS, IVFFLAT for vectors).

### 3. Seed the Database

```bash
python -m app.seed
```

This script:

- Downloads the assessment dataset from the provided S3 URL
- Unwraps DynamoDB‑style `S/N/M/L/NULL` wrappers
- Writes one row per available audio variant
- Groups siblings by `conversion_group_id`
- Populates embeddings with a deterministic placeholder
- Initializes `clicks`, `impressions`, and `created_at`

You should see:

```text
✅ Seeded 96 track variants from real dataset
```

(The exact number may change if the upstream dataset changes, but it will be ≥ 30.)

### 4. Run Search Verification

```bash
python -m scripts.verify
```

This runs a few representative queries (`"new pop"`, `"C major female vocal"`, `"energetic electronic"`) through the full hybrid pipeline and prints the top results with scores and lineage ids.

---
### 5. Run Feedback Buffer Validation (Part 4)

```bash
python -m scripts.simulate_feedback
```

This generates high‑QPS feedback events into `FeedbackBuffer`, flushes the aggregated counters to Postgres, and verifies that `clicks` and `impressions` in `music_tracks` match the expected buffered totals.[file:177][file:174]

---
## Files of Interest

- `docker-compose.yml` — Postgres + pgvector service
- `app/models.py` — SQLAlchemy async models for `music_tracks`
- `app/search.py` — hybrid retrieval logic (fixed from the broken query in the prompt)
- `app/ranking.py` — reranking and diversity functions
- `app/feedback.py` — in‑memory feedback buffer and flush logic
- `app/seed.py` — data ingest from `song_metadata.json` S3 URL
- `scripts/verify.py` — simple CLI to run a few search queries and inspect output
- `scripts/simulate_feedback.py` — stress‑tests the feedback buffer
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

```bash
python -m app.ranking
```

Output:

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
- D with 0/0 is not collapsed to zero due to cold‑start handling.

---

## Notes and Limitations

- Embeddings used here are deterministic placeholders to keep the seeding step self‑contained; in a real deployment, both indexed songs and live queries would use the same production embedding model.
- Diversity is lineage‑based: it prevents multiple variants from the same generation lineage from clustering in the top results, but it does not attempt genre/artist‑level diversification.
- Feedback buffering uses an in‑memory `FeedbackBuffer` that aggregates `click` and `impression` events and periodically flushes batched counter updates to `music_tracks` to reduce row‑level lock contention.[file:174][file:163]  
- The maximum staleness of engagement counters feeding the reranker is roughly the flush interval (about 1 second), which is acceptable given the heuristic nature of the reranking logic.[file:163]  
- Because the buffer is in‑memory, unflushed events can be lost on process restart; a production system would move this path to a durable queue or store (e.g. Redis, Kafka) to avoid data loss.[file:163]  


See `DECISIONS.md` for deeper reasoning and trade‑offs for each part.