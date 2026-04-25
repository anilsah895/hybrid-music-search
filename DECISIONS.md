# Ranking Design

We use a **hybrid scoring system** combining:

- Vector similarity (semantic relevance)
- Full-text search (lexical precision)
- CTR signal (user engagement proxy)
- Freshness (temporal relevance)

---

## Why hybrid instead of single approach?

We explicitly rejected single-signal systems:

- ❌ Pure vector search  
  - Fails on exact constraints like “C major”, “128 BPM”, “female vocal”
  - Cannot reliably handle structured metadata queries

- ❌ Pure full-text search (BM25-like behavior)  
  - Fails on semantic or paraphrased queries like “sad rainy piano”
  - Too brittle for creative music search

👉 A hybrid system is required because user queries span:
- structured musical constraints
- semantic mood descriptions
- mixed intent queries

---

# CTR Design

We use **smoothed CTR instead of raw CTR**.

## Why smoothing is required:

- Prevents cold-start collapse for new tracks (0 impressions problem)
- Reduces volatility for low-traffic songs
- Avoids overfitting to small interaction samples

### Effect:
New uploads remain discoverable while still allowing popular tracks to gain ranking advantage over time.

---

# Freshness Model

We apply **logarithmic time decay**.

## Why log decay:

- Linear decay → too aggressive, destroys long-tail value
- No decay → over-amplifies old viral content

👉 Log decay provides a controlled balance:
- preserves evergreen content
- boosts recent trends without destabilizing ranking

---

# Diversity Strategy

We use **group-based Maximal Marginal Relevance (MMR)**.

## Purpose:

Prevent near-duplicate results from appearing in top ranks.

## Implementation:

- Uses `conversion_group_id` to group sibling audio variations
- Ensures only one representative per generation group is surfaced in top-K results

### Benefit:
Improves result diversity without sacrificing relevance.

---

## Limitation:

- No embedding-aware diversity yet
- Current diversity is structural (group-level), not semantic

👉 Future improvement: semantic MMR using embeddings

---

# Schema Design Decisions

## 1. JSONB usage

We store the following in JSONB:

- `all_tags`
- `raw_payload`
- `extra_metadata`

### Why JSONB:

- Dataset originates from DynamoDB-style export (nested + inconsistent schema)
- Allows flexible schema evolution without migrations
- Preserves full original record for reprocessing

---

## 2. TSVECTOR strategy (weighted full-text search)

We use a weighted search vector generated via trigger:

- Title → weight A (highest priority)
- Acoustic prompt → weight B (medium priority)
- Tags → weight C (lowest priority)

### Why weighting matters:

- Title matches represent strong intent signals
- Acoustic prompt represents descriptive semantic content
- Tags provide weak but broad recall signals

👉 This ensures lexical ranking aligns with user intent strength.

---

## 3. Vector storage design

- Embeddings stored using `pgvector`
- Indexed with IVFFLAT for approximate nearest neighbor search

### Why separate vector column:

- Keeps semantic retrieval independent from relational schema
- Enables scalable similarity search
- Supports hybrid ranking with full-text signals

---

## 4. Maintainability decision

We retain `raw_payload` as JSONB to ensure:

- Full traceability of original ingestion data
- Ability to reprocess or rebuild schema without re-fetching dataset
- Future-proofing against schema evolution in upstream pipeline

---

# Known Limitations

- No personalized CTR (global aggregation only)
- No embedding-based diversity reranking
- Freshness is not user-context aware
- JSONB fields are intentionally unindexed for flexibility (trade-off: slower filtering if used directly)

---

# Part 2 — Broken Search Debugging inside app/search.py

## Problem Summary

The original hybrid search query failed on keyword-heavy queries such as "C major", "128 BPM", and "female vocal", even though these terms existed in the dataset.

At the same time, semantic queries like "sad rainy piano" worked correctly.

This indicated a breakdown in full-text search handling and fusion logic.

---

## Bug 1 — FTS Handling Issue

The system used `to_tsquery`, which is too strict and not suitable for natural or structured queries.

### Why this is a problem:

- It enforces rigid boolean parsing of user input
- Breaks structured phrases like "128 BPM" into meaningless tokens
- Does not preserve phrase intent or numeric meaning

### User impact:

- Exact keyword queries fail despite valid data existing
- Users feel the system does not understand precise constraints
- Search behaves unpredictably for structured music metadata queries

---

## Bug 2 — Fusion Logic Problem

The original fusion relied only on:

- row-number based ranking from vector and FTS results

### Why this is a problem:

- Rank position is not a true relevance signal
- Sensitive to LIMIT truncation in subqueries
- Does not reflect magnitude differences in similarity or text relevance

### User impact:

- Relevant keyword matches are buried
- Small ranking shifts cause large ordering changes
- Results feel unstable and inconsistent across similar queries

---

## Fix Summary

We corrected the system by:

- Switching to safer `plainto_tsquery` parsing
- Ensuring correct filtering using `search_vector @@ query`
- Introducing weighted score fusion:
  - vector similarity (0.6)
  - text relevance (0.4)

---

## Resulting Improvement

After the fix:

- Exact keyword queries correctly surface matching songs
- Semantic queries remain strong via vector search
- Hybrid queries correctly balance both signals
- Ranking becomes stable and interpretable