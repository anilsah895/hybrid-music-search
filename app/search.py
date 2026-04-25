import asyncio
from sqlalchemy import text
from app.database import SessionLocal


# -----------------------------
# convert python list → pgvector string
# -----------------------------
def format_embedding(embedding: list[float]) -> str:
    return "[" + ",".join(map(str, embedding)) + "]"


# -----------------------------
# HYBRID SEARCH
# -----------------------------
async def hybrid_search(session, query: str, embedding: str, limit: int = 20):

    sql = text("""
        WITH q AS (
            SELECT plainto_tsquery('english', :query) AS fts_query
        ),

        vector_candidates AS (
            SELECT
                id,
                embedding <=> CAST(:embedding AS vector) AS distance,
                ROW_NUMBER() OVER (
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                ) AS v_rank
            FROM music_tracks
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT 200
        ),

        fts_candidates AS (
            SELECT
                id,
                ts_rank(search_vector, q.fts_query) AS text_score,
                ROW_NUMBER() OVER (
                    ORDER BY ts_rank(search_vector, q.fts_query) DESC
                ) AS f_rank
            FROM music_tracks, q
            WHERE search_vector @@ q.fts_query
            LIMIT 200
        ),

        fused AS (
            SELECT
                COALESCE(v.id, f.id) AS id,
                (1.0 / (60 + COALESCE(v.v_rank, 200))) +
                (1.0 / (60 + COALESCE(f.f_rank, 200))) AS score
            FROM vector_candidates v
            FULL OUTER JOIN fts_candidates f ON v.id = f.id
        )

        SELECT
            m.*,
            fused.score
        FROM fused
        JOIN music_tracks m ON m.id = fused.id
        ORDER BY fused.score DESC
        LIMIT :limit;
    """)

    result = await session.execute(sql, {
        "query": query,
        "embedding": embedding,
        "limit": limit
    })

    return result.mappings().all()


# -----------------------------
# SYNC WRAPPER (SAFE)
# -----------------------------
def search(query: str, embedding: list[float] | None = None):

    if embedding is None:
        embedding = [0.1] * 1536  # placeholder

    embedding_str = format_embedding(embedding)

    async def _run():
        async with SessionLocal() as session:
            return await hybrid_search(session, query, embedding_str)

    return asyncio.run(_run())