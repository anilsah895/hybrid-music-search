import uuid
from app.database import SessionLocal
from app.models import MusicTrack


async def seed():

    session = SessionLocal()

    # FIXED groups for evaluation

    group1 = uuid.uuid4()
    group2 = uuid.uuid4()

    data = [
        # MUST WIN fresh CTR case
        ("new pop hit", 10, 500, 10, group1, 10),

        # old viral song
        ("old pop hit", 400, 2000, 350, group1, 350),

        # lexical rescue case
        ("C major female vocal acoustic ballad", 50, 600, 5, group2, 50),

        # unrelated noise
        ("dark techno bass", 100, 900, 20, group2, 100),
    ]

    for i in range(30):
        title, clicks, imp, age, gid, idx = data[i % len(data)]

        session.add(MusicTrack(
            external_id=f"t{i}",
            title=title,
            acoustic_prompt_descriptive=title,
            all_tags=title.split(),
            extra_metadata={},   

            embedding=[0.1] * 1536,

            conversion_group_id=gid,
            conversion_index=idx,

            clicks=clicks,
            impressions=imp,
            created_at=365 - age
        ))

    await session.commit()


import asyncio

if __name__ == "__main__":
    asyncio.run(seed())