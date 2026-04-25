import asyncio
from collections import defaultdict
from sqlalchemy import text


class FeedbackBuffer:
    def __init__(self):
        # In-memory aggregation buffer:
        # track_id -> {"clicks": int, "impressions": int}
        #
        # We separate clicks and impressions because CTR depends on both.
        # Mixing all feedback into one counter would weaken ranking quality.
        self.buffer = defaultdict(lambda: {"clicks": 0, "impressions": 0})

        # Async lock protects the shared in-memory buffer from race conditions
        # when many concurrent requests try to record feedback at once.
        self.lock = asyncio.Lock()

    async def record(self, track_id: str, event_type: str):
        """
        Called by the feedback endpoint.
        This method is intentionally lightweight because it sits on the hot path.
        """

        async with self.lock:
            # Click = stronger engagement signal
            if event_type == "click":
                self.buffer[track_id]["clicks"] += 1

            # Impression = exposure signal used as denominator for CTR-like features
            elif event_type == "impression":
                self.buffer[track_id]["impressions"] += 1

            else:
                # Ignore unknown event types so bad client data does not pollute ranking
                return

    async def flush(self, session):
        """
        Batch flush aggregated counters to Postgres.

        Why batching helps:
        - avoids one UPDATE per request
        - reduces row-level lock contention
        - keeps request latency low under higher throughput
        """

        # Snapshot the current buffer under lock so new events can continue
        # flowing into a fresh buffer immediately.
        async with self.lock:
            batch = dict(self.buffer)
            self.buffer = defaultdict(lambda: {"clicks": 0, "impressions": 0})

        # Nothing to flush -> exit early
        if not batch:
            return

        # Apply all accumulated increments.
        # We do DB work outside the lock so the hot path stays fast.
        for track_id, counts in batch.items():
            await session.execute(
                text("""
                    UPDATE music_tracks
                    SET
                        clicks = clicks + :clicks,
                        impressions = impressions + :impressions
                    WHERE id = :id
                """),
                {
                    "id": track_id,
                    "clicks": counts["clicks"],
                    "impressions": counts["impressions"],
                },
            )

        # Commit once per batch, not once per event.
        # This is much cheaper and scales better.
        await session.commit()