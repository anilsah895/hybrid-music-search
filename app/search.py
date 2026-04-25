from sqlalchemy import text


async def hybrid_search(session, query: str, embedding: list[float], limit: int = 20):

    sql = text("""
        WITH q AS (
            -- safer NLP-friendly parsing (not overly strict)
            SELECT plainto_tsquery('english', :query) AS fts_query
        ),

        -- VECTOR SEARCH (candidate retrieval first)
        vector_candidates AS (
            SELECT
                id,
                embedding <=> :embedding AS distance,
                ROW_NUMBER() OVER (ORDER BY embedding <=> :embedding) AS v_rank
            FROM music_tracks
            ORDER BY embedding <=> :embedding
            LIMIT 200
        ),

        -- FTS SEARCH (separate candidate pool)
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

        -- HYBRID FUSION (rank-based, stable)
        fused AS (
            SELECT
                COALESCE(v.id, f.id) AS id,

                -- normalized reciprocal rank fusion
                (
                    1.0 / (60 + COALESCE(v.v_rank, 200))
                ) +
                (
                    1.0 / (60 + COALESCE(f.f_rank, 200))
                ) AS score

            FROM vector_candidates v
            FULL OUTER JOIN fts_candidates f
            ON v.id = f.id
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