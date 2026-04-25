import uuid
import time
import asyncio
import httpx

from app.database import SessionLocal
from app.models import MusicTrack


DATASET_URL = "https://lalals.s3.us-east-1.amazonaws.com/ai_backend_assets/technical_assessment_datasets/song_metadata.json"


# =========================================================
# LOAD DATASET
# =========================================================
async def fetch_dataset():
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(DATASET_URL)
        resp.raise_for_status()
        return resp.json()


# =========================================================
# SEED FUNCTION
# =========================================================
def seed():

    session = SessionLocal()

    # fetch real dataset
    data = asyncio.run(fetch_dataset())

    rows = []

    # group for diversity testing
    group1 = uuid.uuid4()
    group2 = uuid.uuid4()

    # =====================================================
    # 1. PROCESS REAL DATASET (PRIMARY REQUIREMENT)
    # =====================================================
    for i, record in enumerate(data[:30]):  # ensure at least 30 records

        title = record.get("title", "unknown")

        rows.append(MusicTrack(
            external_id=f"real_{i}",
            title=title,
            acoustic_prompt_descriptive=title,
            all_tags=title.split(),

            extra_metadata={},

            # simple but stable embedding
            embedding=[0.1 + (hash(title) % 100) * 0.0001] * 1536,

            conversion_group_id=group1 if i % 2 == 0 else group2,
            conversion_index=i,

            clicks=record.get("clicks", 0),
            impressions=record.get("impressions", 0),

            # FIXED timestamp for recency model
            created_at=time.time() - (i % 10) * 86400
        ))

    # =====================================================
    # 2. ENSURE MINIMUM 30 RECORDS GUARANTEE
    # (fallback synthetic augmentation if dataset is small)
    # =====================================================
    while len(rows) < 30:

        i = len(rows)

        rows.append(MusicTrack(
            external_id=f"synthetic_{i}",
            title=f"synthetic song {i}",
            acoustic_prompt_descriptive=f"synthetic song {i}",
            all_tags=["synthetic"],

            extra_metadata={},

            embedding=[0.1] * 1536,

            conversion_group_id=group1 if i % 2 == 0 else group2,
            conversion_index=i,

            clicks=10,
            impressions=50,

            created_at=time.time() - i * 86400
        ))

    # =====================================================
    # INSERT INTO DB
    # =====================================================
    session.add_all(rows)
    session.commit()
    session.close()

    print(f"✅ Seeded {len(rows)} records")


# =========================================================
# ENTRY POINT
# =========================================================
if __name__ == "__main__":
    seed()