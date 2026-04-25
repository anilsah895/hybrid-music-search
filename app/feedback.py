import asyncio
from collections import defaultdict


class FeedbackBuffer:
    def __init__(self):
        # We separate clicks and impressions because CTR = clicks / impressions
        # Mixing them would break ranking logic in Part 3 completely.
        self.buffer = defaultdict(lambda: {"clicks": 0, "impressions": 0})

        # Async lock ensures safe updates under high concurrency (5,000 RPS)
        # Prevents race conditions when multiple requests update buffer simultaneously
        self.lock = asyncio.Lock()

    async def record(self, track_id: str, event_type: str):
        """
        Called by POST /feedback endpoint.
        This is the hot path (high QPS), so we keep it extremely lightweight.
        """

        async with self.lock:
            # We explicitly separate event types to preserve signal integrity
            # Clicks = strong engagement signal
            # Impressions = exposure signal (denominator for CTR)
            if event_type == "click":
                self.buffer[track_id]["clicks"] += 1

            elif event_type == "impression":
                self.buffer[track_id]["impressions"] += 1

            else:
                # Unknown events are ignored to avoid polluting ranking data
                return

    async def flush(self, session):
        """
        Batch flush to Postgres.
        Goal: eliminate row-level contention by aggregating updates in memory first.
        """

        # Snapshot buffer under lock to ensure consistency
        # We immediately replace it to avoid blocking incoming requests
        async with self.lock:
            batch = self.buffer
            self.buffer = defaultdict(lambda: {"clicks": 0, "impressions": 0})

        # DB writes happen outside lock so ingestion is never blocked
        # This is critical for maintaining low latency under load
        for track_id, counts in batch.items():

            await session.execute(
                """
                UPDATE music_tracks
                SET clicks = clicks + :clicks,
                    impressions = impressions + :impressions
                WHERE id = :id
                """,
                {
                    "id": track_id,
                    "clicks": counts["clicks"],
                    "impressions": counts["impressions"],
                }
            )

        # Commit once per flush (not per event → major performance gain)
        await session.commit()