from sqlalchemy import text


async def hybrid_search(session, query, embedding):

    sql = text("""
        SELECT *,
        1 - (embedding <=> :embedding) AS vector_score,
        ts_rank(search_vector, plainto_tsquery(:query)) AS text_score
        FROM music_tracks
    """)

    result = await session.execute(sql, {
        "embedding": embedding,
        "query": query
    })

    rows = result.mappings().all()  # FIXED

    return rows