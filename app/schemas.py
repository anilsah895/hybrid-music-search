from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class MusicIngestSchema(BaseModel):
    title: str
    artist: Optional[str]
    acoustic_prompt_descriptive: Optional[str]
    all_tags: List[str] = []
    raw_item: Dict[str, Any]


class SearchQuery(BaseModel):
    query: str
    top_k: int = 10