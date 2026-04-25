import uuid
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Text, Integer, String, Index, DateTime, func, text
from sqlalchemy.dialects.postgresql import UUID, JSONB, TSVECTOR
from pgvector.sqlalchemy import Vector

# Base class for all ORM models
Base = declarative_base()


class MusicTrack(Base):
    # Keep the existing table name so the rest of your repo does not break
    __tablename__ = "music_tracks"

    # Primary key for each stored track/variant row
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Original source ID from the dataset; unique so duplicate ingests are prevented
    external_id = Column(String, unique=True, index=True, nullable=False)

    # Core searchable text fields
    title = Column(Text, nullable=False)
    acoustic_prompt_descriptive = Column(Text)

    # Flexible metadata fields kept in JSONB because the source schema can evolve
    # We provide safe defaults so inserts do not fail when fields are missing
    all_tags = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    extra_metadata = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    raw_payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    # Groups sibling outputs from the same generation lineage
    # This is important because conversion_path_1 and conversion_path_2 are related variations
    conversion_group_id = Column(UUID(as_uuid=True), index=True, nullable=False)

    # Position of the sibling variant within the group (for example 0/1 or 1/2)
    conversion_index = Column(Integer, nullable=False, server_default=text("0"))

    # Dense vector used for semantic similarity search
    embedding = Column(Vector(1536))

    # Precomputed full-text search column populated by a DB trigger
    search_vector = Column(TSVECTOR)

    # Lightweight behavioral counters kept for now because your current repo already uses them
    # These can later be complemented by a dedicated search_events table
    clicks = Column(Integer, nullable=False, server_default=text("0"))
    impressions = Column(Integer, nullable=False, server_default=text("0"))

    # Real timestamp instead of Integer so freshness/decay logic works correctly
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        # Proper ANN index for pgvector nearest-neighbor search
        # A plain B-tree index is not useful for vector similarity
        Index(
            "ix_vec",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"lists": 100},
        ),

        # GIN index for fast Postgres full-text search over the TSVECTOR column
        Index("ix_fts", "search_vector", postgresql_using="gin"),

        # Trigram index improves fuzzy/typo-tolerant title matching
        Index(
            "ix_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),

        # Fast lookup for deduping/reranking sibling variants from the same generation group
        Index("ix_group", "conversion_group_id"),
    )