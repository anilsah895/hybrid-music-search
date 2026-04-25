import asyncio
from app.search import search_async


async def run(query: str):
    print("\n" + "=" * 60)
    print(f"QUERY: {query}")
    print("=" * 60)

    results = await search_async(query)

    for i, row in enumerate(results[:5], 1):
        print(f"{i}. {row['title']} | score={row['score']:.4f}")


async def main():
    await run("new pop")
    await run("C major female vocal")
    await run("energetic electronic")


if __name__ == "__main__":
    asyncio.run(main())