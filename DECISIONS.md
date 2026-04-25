# Ranking Design

We use a **hybrid scoring system** combining:

- Vector similarity (semantic relevance)
- Full-text search (lexical precision)
- CTR signal (user preference / engagement)
- Freshness (temporal relevance)

## Why hybrid instead of single approach?

We rejected:

- ❌ Pure vector search  
  - Misses exact keyword-based queries (e.g., “C major female vocal”)

- ❌ Pure BM25 / full-text search  
  - Misses semantic intent (e.g., paraphrased queries)

👉 Hybrid retrieval ensures both semantic understanding and lexical precision.

---

# CTR Design

We use **smoothed CTR** instead of raw CTR.

## Why smoothing is required:

- Prevents cold-start bias for new tracks
- Avoids instability for low-impression items
- Produces stable ranking signals

👉 This ensures fair ranking across both new and popular content.

---

# Freshness Model

We apply **logarithmic time decay**.

## Why log decay:

- Linear decay → too aggressive (kills older viral content)
- No decay → over-rewards stale content

👉 Log decay balances:
- long-term relevance
- recent trend boosting

---

# Diversity Strategy

We use **group-based Maximal Marginal Relevance (MMR)**.

## Goal:

Avoid duplicate results from the same generation family.

## Implementation:

- Uses `conversion_group_id` to group sibling conversions
- Ensures only one representative per group appears in top results

## Limitation:

- No embedding-based diversity yet  
- Future improvement: semantic diversity-aware ranking

---

# Schema Design Decisions

## 1. JSONB usage

We use JSONB for:

- `all_tags`
- `raw_payload`
- `extra_metadata`

### Why JSONB:

- Dataset is DynamoDB-style (inconsistent schema)
- Allows flexible future evolution
- Avoids frequent schema migrations

---

## 2. TSVECTOR strategy

`search_vector` is auto-generated via trigger:

It combines:

- Title → weight A (highest priority)
- Acoustic prompt → weight B (medium priority)
- Tags → weight C (lowest priority)

### Why weighting matters:

- Title matches should dominate ranking
- Tags act as weak signals for recall

---

## 3. Vector storage design

- Embeddings stored in a dedicated `pgvector` column
- Indexed using IVFFLAT

### Why separate column:

- Decouples semantic search from relational schema
- Enables scalable ANN search
- Works alongside full-text search for hybrid retrieval

---

# Known Limitations

- No embedding-based diversity reranking yet
- CTR is global (not user-personalized)
- Freshness is not user-context aware
- JSONB fields are not indexed (intentional tradeoff for flexibility)

---

# Summary

This system prioritizes:

- High-quality hybrid retrieval (semantic + lexical)
- Scalable indexing strategy (GIN + IVFFLAT)
- Flexible schema evolution (JSONB + raw payload)
- Production safety (fallbacks + smoothing mechanisms)