import asyncio
from app.feedback import FeedbackBuffer


async def simulate():
    fb = FeedbackBuffer()

    async def send_events():
        for _ in range(10000):
            await fb.record_click("song_1")

    tasks = [send_events() for _ in range(10)]

    await asyncio.gather(*tasks)

    print("Buffered clicks:", fb.buffer["song_1"])


asyncio.run(simulate())