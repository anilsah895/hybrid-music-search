import asyncio
from collections import defaultdict


class FeedbackBuffer:

    def __init__(self):
        self.buffer = defaultdict(int)
        self.lock = asyncio.Lock()

    async def record_click(self, track_id):
        async with self.lock:
            self.buffer[track_id] += 1

    async def flush(self, session):

        async with self.lock:
            for tid, c in self.buffer.items():
                await session.execute(
                    """
                    UPDATE music_tracks
                    SET clicks = clicks + :c,
                        impressions = impressions + 1
                    WHERE id = :id
                    """,
                    {"c": c, "id": tid}
                )

            self.buffer.clear()
            await session.commit()