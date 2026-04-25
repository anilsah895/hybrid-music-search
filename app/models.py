import uuid
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Text, Integer, String, JSON, Index, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB, TSVECTOR
from pgvector.sqlalchemy import Vector

Base = declarative_base()


class MusicTrack(Base):
    __tablename__ = "music_tracks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id = Column(String, unique=True, index=True)

    title = Column(Text, nullable=False)
    acoustic_prompt_descriptive = Column(Text)

    all_tags = Column(JSONB)
    extra_metadata = Column(JSONB)
    raw_payload = Column(JSONB)

    conversion_group_id = Column(UUID(as_uuid=True), index=True)
    conversion_index = Column(Integer, default=0)

    embedding = Column(Vector(1536))

    search_vector = Column(TSVECTOR)

    clicks = Column(Integer, default=0)
    impressions = Column(Integer, default=0)

    created_at = Column(Integer)  # days since epoch (simplified)

    __table_args__ = (
        Index("ix_vec", "embedding"),
        Index("ix_fts", "search_vector", postgresql_using="gin"),
        Index("ix_group", "conversion_group_id"),
    )