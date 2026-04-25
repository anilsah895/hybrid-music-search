from sqlalchemy import text


async def hybrid_search(session, query, embedding, limit=20):

    sql = text("""
        WITH fts AS (
            SELECT *,
                   ts_rank(search_vector, plainto_tsquery('english', :query)) AS text_score
            FROM music_tracks
            WHERE search_vector @@ plainto_tsquery('english', :query)
        ),
        vec AS (
            SELECT *,
                   1 - (embedding <=> :embedding) AS vector_score
            FROM music_tracks
        ),
        fused AS (
            SELECT
                COALESCE(vec.id, fts.id) AS id,
                vec.vector_score,
                fts.text_score,
                (
                    0.6 * COALESCE(vec.vector_score, 0) +
                    0.4 * COALESCE(fts.text_score, 0)
                ) AS score
            FROM vec
            FULL OUTER JOIN fts ON vec.id = fts.id
        )
        SELECT *
        FROM music_tracks m
        JOIN fused f ON m.id = f.id
        ORDER BY f.score DESC
        LIMIT :limit;
    """)

    result = await session.execute(sql, {
        "embedding": embedding,
        "query": query,
        "limit": limit
    })

    return result.mappings().all()