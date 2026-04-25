import pytest
from app.feedback import FeedbackBuffer


class FakeSession:
    def __init__(self):
        self.updates = []

    async def execute(self, query, params):
        self.updates.append(params)

    async def commit(self):
        pass


@pytest.mark.asyncio
async def test_buffer_flush():
    fb = FeedbackBuffer()
    session = FakeSession()

    # -------------------------
    # CLICK EVENTS
    # -------------------------
    await fb.record("song_1", "click")
    await fb.record("song_1", "click")
    await fb.record("song_2", "click")

    # -------------------------
    # IMPRESSION EVENTS (NEW PART)
    # -------------------------
    await fb.record("song_1", "impression")
    await fb.record("song_1", "impression")

    await fb.flush(session)

    # -------------------------
    # VERIFY CLICK AGGREGATION
    # -------------------------
    assert {
        "id": "song_1",
        "clicks": 2,
        "impressions": 2
    } in session.updates

    assert {
        "id": "song_2",
        "clicks": 1,
        "impressions": 0
    } in session.updates

    # -------------------------
    # BUFFER SHOULD BE CLEARED
    # -------------------------
    assert len(fb.buffer) == 0