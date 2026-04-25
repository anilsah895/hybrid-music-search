# import asyncio
# from app.search import search_async


# def print_row(idx: int, row: dict):
#     # Adjust keys to whatever your search_async returns
#     title = row.get("title")
#     score = row.get("score")
#     clicks = row.get("clicks")
#     impressions = row.get("impressions")
#     created_at = row.get("created_at")
#     group_id = row.get("conversion_group_id")

#     print(
#         f"{idx}. {title} | score={score:.4f} | "
#         f"clicks={clicks} | impressions={impressions} | "
#         f"created_at={created_at} | group={group_id}"
#     )


# async def run(query: str, limit: int = 5):
#     print("\n" + "=" * 60)
#     print(f"QUERY: {query}")
#     print("=" * 60)

#     results = await search_async(query)

#     for i, row in enumerate(results[:limit], 1):
#         print_row(i, row)


# async def main():
#     # 1) Recency vs raw clicks: show reranker behavior
#     await run("new pop")

#     # 2) Lexical technical intent: show FTS catching C major + female vocal
#     await run("C major female vocal")

#     # 3) Vibe / semantic query for completeness
#     await run("energetic electronic")


# if __name__ == "__main__":
#     asyncio.run(main())


import asyncio
from app.search import search_async


def print_row(idx: int, row: dict):
    print(
        f"{idx}. id={row.get('id')} "
        f"| external_id={row.get('external_id')} "
        f"| title={row.get('title')} "
        f"| group={row.get('conversion_group_id')} "
        f"| idx={row.get('conversion_index')} "
        f"| score={row.get('score'):.4f}"
    )


async def run(query: str, limit: int = 10):
    print("\n" + "=" * 60)
    print(f"QUERY: {query}")
    print("=" * 60)

    results = await search_async(query)

    for i, row in enumerate(results[:limit], 1):
        print_row(i, row)


async def main():
    await run("new pop")
    await run("C major female vocal")


if __name__ == "__main__":
    asyncio.run(main())