import asyncio
import os
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.feedback import FeedbackBuffer


# Use the same DB as alembic.ini, but async driver
ASYNC_DATABASE_URL = os.getenv(
    "ASYNC_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/music_search",
)


def get_async_session_factory():
    engine = create_async_engine(ASYNC_DATABASE_URL, future=True)
    return engine, async_sessionmaker(bind=engine, expire_on_commit=False)


async def fetch_sample_tracks(async_session_factory, limit: int = 5):
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, external_id, title, clicks, impressions "
                    "FROM music_tracks ORDER BY title LIMIT :limit"
                ),
                {"limit": limit},
            )
        ).fetchall()
    return rows


async def generate_events(
    fb: FeedbackBuffer,
    track_ids: list[str],
    clicks_per_track: int,
    impressions_per_track: int,
):
    async def spam(track_id: str):
        for _ in range(impressions_per_track):
            await fb.record(track_id, "impression")
        for _ in range(clicks_per_track):
            await fb.record(track_id, "click")

    tasks = [asyncio.create_task(spam(track_id)) for track_id in track_ids]
    await asyncio.gather(*tasks)


async def main():
    engine, AsyncSessionFactory = get_async_session_factory()

    # 1) Pick a few tracks and record baseline counts
    rows = await fetch_sample_tracks(AsyncSessionFactory, limit=5)
    if not rows:
        print("No rows found in music_tracks. Run app.seed first.")
        await engine.dispose()
        return

    selected = [
        (str(r.id), r.external_id, r.title, r.clicks or 0, r.impressions or 0)
        for r in rows
    ]
    track_ids = [r[0] for r in selected]

    fb = FeedbackBuffer()
    clicks_per_track = 2000
    impressions_per_track = 5000

    # 2) Generate in-memory feedback events (high QPS)
    start = time.perf_counter()
    await generate_events(fb, track_ids, clicks_per_track, impressions_per_track)
    buffered_elapsed = time.perf_counter() - start

    print("=" * 80)
    print("PART 4 VALIDATION: BUFFERED FEEDBACK")
    print("=" * 80)
    print(f"Tracks selected: {len(track_ids)}")
    print(
        f"Total events generated: "
        f"{len(track_ids) * (clicks_per_track + impressions_per_track)}"
    )
    print(f"Buffering time: {buffered_elapsed:.4f}s")
    print()

    for track_id, external_id, title, _, _ in selected:
        counts = fb.buffer[track_id]
        print(
            f"Buffered -> {external_id} | {title} | "
            f"clicks={counts['clicks']} impressions={counts['impressions']}"
        )

    # 3) Flush aggregated counters to DB using async session
    async with AsyncSessionFactory() as session:
        flush_start = time.perf_counter()
        await fb.flush(session)
        flush_elapsed = time.perf_counter() - flush_start

        after_rows = (
            await session.execute(
                text(
                    "SELECT id, external_id, title, clicks, impressions "
                    "FROM music_tracks WHERE id = ANY(:ids) ORDER BY title"
                ),
                {"ids": track_ids},
            )
        ).fetchall()

    print()
    print(f"Flush time: {flush_elapsed:.4f}s")
    print()
    print("DB verification:")

    before_map = {
        track_id: {"clicks": clicks, "impressions": impressions}
        for track_id, _, _, clicks, impressions in selected
    }

    all_ok = True
    for row in after_rows:
        track_id = str(row.id)
        prev = before_map[track_id]
        expected_clicks = prev["clicks"] + clicks_per_track
        expected_impressions = prev["impressions"] + impressions_per_track

        ok = (row.clicks == expected_clicks) and (
            row.impressions == expected_impressions
        )
        all_ok = all_ok and ok
        status = "OK" if ok else "MISMATCH"

        print(
            f"{status} -> {row.external_id} | {row.title} | "
            f"clicks={row.clicks} (expected {expected_clicks}) | "
            f"impressions={row.impressions} (expected {expected_impressions})"
        )

    print()
    if all_ok:
        print(
            "SUCCESS: Part 4 validation passed. "
            "Events were buffered in memory and flushed as batched counter updates."
        )
    else:
        print("FAILURE: Part 4 validation found mismatched DB counts.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())