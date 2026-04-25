import asyncio
from sqlalchemy import text
from app.database import SessionLocal


def format_embedding(embedding: list[float]) -> str:
    # pgvector accepts text input in the form: [0.1,0.2,...]
    # We keep this helper because your current repo already passes embeddings as strings into SQL.
    return "[" + ",".join(map(str, embedding)) + "]"


def dedupe_results(results, limit=5):
    # We only want one representative result per generation lineage.
    # If conversion_group_id is missing, we fall back to normalized title text.
    seen = set()
    final = []

    # Stable ordering: highest score first, then ID as deterministic tie-break
    for r in sorted(results, key=lambda x: (-x["score"], str(x.get("id")))):
        key = r.get("conversion_group_id") or r.get("title", "").strip().lower()

        if key in seen:
            continue

        seen.add(key)
        final.append(r)

        if len(final) >= limit:
            break

    return final


async def hybrid_search(session, query: str, embedding: str, limit: int = 20):
    # Hybrid retrieval strategy:
    # 1. pull semantic candidates using vector similarity
    # 2. pull lexical candidates using Postgres full-text search
    # 3. fuse both ranked lists with reciprocal-rank-style scoring
    # 4. keep only the best sibling per conversion_group_id
    sql = text("""
    WITH q AS (
        -- Build the text search query once and reuse it
        SELECT plainto_tsquery('english', :query) AS fts_query
    ),

    vector_candidates AS (
        SELECT
            id,
            -- Cosine distance from pgvector; lower is better
            embedding <=> CAST(:embedding AS vector) AS distance,

            -- Rank vector matches explicitly so we can fuse rank positions later
            ROW_NUMBER() OVER (
                ORDER BY embedding <=> CAST(:embedding AS vector)
            ) AS v_rank
        FROM music_tracks
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:embedding AS vector)
        LIMIT 200
    ),

    fts_candidates AS (
        SELECT
            id,

            -- Lexical relevance score from full-text search
            ts_rank_cd(search_vector, q.fts_query) AS text_score,

            -- Rank lexical matches explicitly for fusion
            ROW_NUMBER() OVER (
                ORDER BY ts_rank_cd(search_vector, q.fts_query) DESC, id
            ) AS f_rank
        FROM music_tracks, q
        WHERE search_vector @@ q.fts_query
        LIMIT 200
    ),

    fused AS (
        SELECT
            COALESCE(v.id, f.id) AS id,

            -- Reciprocal-rank style fusion:
            -- items that rank highly in either list get boosted
            (1.0 / (10 + COALESCE(v.v_rank, 200))) +
            (1.0 / (10 + COALESCE(f.f_rank, 200))) AS score
        FROM vector_candidates v
        FULL OUTER JOIN fts_candidates f ON v.id = f.id
    ),

    best_per_conversion AS (
        SELECT DISTINCT ON (m.conversion_group_id)
            m.*,
            fused.score,

            -- Expose component scores for later ranking/debugging
            COALESCE(1 - vc.distance, 0.0) AS vector_score,
            COALESCE(fc.text_score, 0.0) AS text_score
        FROM fused
        JOIN music_tracks m ON m.id = fused.id
        LEFT JOIN vector_candidates vc ON vc.id = fused.id
        LEFT JOIN fts_candidates fc ON fc.id = fused.id

        -- DISTINCT ON keeps the top-scoring sibling within each conversion group
        ORDER BY m.conversion_group_id, fused.score DESC, m.id
    )

    SELECT *
    FROM best_per_conversion
    ORDER BY score DESC, id
    LIMIT :limit
    """)

    result = await session.execute(sql, {
        "query": query,
        "embedding": embedding,
        "limit": limit
    })

    rows = result.mappings().all()

    # Extra safety: dedupe again in Python in case future SQL changes
    # or null conversion_group_id values slip through.
    return dedupe_results(rows, limit=limit)


async def search_async(query: str, embedding: list[float] | None = None):
    # Fallback embedding keeps the function callable in local testing
    # even if a real embedding model is not wired yet.
    if embedding is None:
        embedding = [0.1] * 1536

    embedding_str = format_embedding(embedding)

    async with SessionLocal() as session:
        return await hybrid_search(session, query, embedding_str)


def search(query: str, embedding: list[float] | None = None):
    # Sync wrapper for scripts/tests that do not want to manage the event loop directly
    return asyncio.run(search_async(query, embedding))