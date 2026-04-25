import asyncio
import uuid
from typing import Any, Dict, List

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from app.database import engine
from app.models import MusicTrack


DATASET_URL = "https://lalals.s3.us-east-1.amazonaws.com/ai_backend_assets/technical_assessment_datasets/song_metadata.json"


# -------------------------
# DynamoDB Parser
# -------------------------
def unwrap(obj: Any):
    """Recursively unwrap DynamoDB JSON format"""
    if isinstance(obj, dict):
        if "S" in obj:
            return obj["S"]
        if "N" in obj:
            n = obj["N"]
            return int(n) if n.isdigit() else float(n)
        if "L" in obj:
            return [unwrap(x) for x in obj["L"]]
        if "M" in obj:
            return {k: unwrap(v) for k, v in obj["M"].items()}
        if "NULL" in obj:
            return None
    return obj


# -------------------------
# Fake embedding (replace with real model later)
# -------------------------
def fake_embedding(text: str):
    return [0.1] * 1536 if text else None


# -------------------------
# Transform record
# -------------------------
def transform(record: Dict) -> List[Dict]:
    sm = record.get("search_metadata", {})

    acoustic = unwrap(sm.get("acoustic_prompt_descriptive"))
    tags = unwrap(sm.get("all_tags", [])) or []

    if not isinstance(tags, list):
        tags = []

    technical = unwrap(sm.get("technical", {})) or {}
    bpm = technical.get("bpm")
    key = technical.get("key")

    # -------------------------
    # EMBEDDING FALLBACK STRATEGY (IMPORTANT)
    # -------------------------
    text_for_embedding = (
        acoustic
        or record.get("prompt")
        or record.get("sounds_1")
        or record.get("title")
    )

    embedding = fake_embedding(text_for_embedding)

    group_id = uuid.uuid4()

    base = {
        "external_id": record.get("id"),
        "title": record.get("title"),
        "acoustic_prompt_descriptive": acoustic,
        "all_tags": tags,

        # IMPORTANT: match your schema
        "extra_metadata": {
            "bpm": bpm,
            "key": key,
        },

        "raw_payload": record,
        "conversion_group_id": group_id,
        "clicks": 0,
        "impressions": 0,
        "created_at": 0,

        # embedding for vector search
        "embedding": embedding,
    }

    rows = []

    if record.get("conversion_path_1"):
        rows.append({
            **base,
            "id": uuid.uuid4(),
            "conversion_index": 1,
        })

    if record.get("conversion_path_2"):
        rows.append({
            **base,
            "id": uuid.uuid4(),
            "conversion_index": 2,
        })

    return rows


# -------------------------
# Fetch dataset
# -------------------------
async def fetch_dataset():
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(DATASET_URL)
        resp.raise_for_status()
        return resp.json()


# -------------------------
# Ingest pipeline
# -------------------------
async def ingest():
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    print("⬇️ Fetching dataset...")
    data = await fetch_dataset()
    print(f"✅ Loaded {len(data)} records")

    all_rows = []
    for record in data:
        all_rows.extend(transform(record))

    print(f"🔄 Transformed into {len(all_rows)} rows (with conversions)")

    async with Session() as session:
        async with session.begin():
            session.add_all([MusicTrack(**row) for row in all_rows])

    print("✅ Ingestion complete")


# -------------------------
# Entry point
# -------------------------
if __name__ == "__main__":
    asyncio.run(ingest())