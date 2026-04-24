## Overview
Hybrid music search system using:
- Vector search (pgvector)
- Full-text search (Postgres FTS)
- Behavioral ranking layer
- Diversity-aware reranking

## Architecture
- Postgres + pgvector
- Async FastAPI backend
- Hybrid retrieval + reranking pipeline

## Key design decisions
- generation_id groups audio variants
- embedding fallback strategy
- JSONB used for evolving metadata