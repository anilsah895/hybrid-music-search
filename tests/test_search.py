import pytest
from app.search import search_async


@pytest.mark.asyncio
async def test_search_results_are_deduped():
    results = await search_async("energetic electronic")

    generation_ids = [r["generation_id"] for r in results if r.get("generation_id") is not None]
    assert len(generation_ids) == len(set(generation_ids))