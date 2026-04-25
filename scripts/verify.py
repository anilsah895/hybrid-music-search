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