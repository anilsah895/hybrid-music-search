from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

# Alembic revision identifiers
revision = "001_init_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Enable extensions required for:
    # - pgvector similarity search
    # - trigram-based fuzzy text matching
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # Create the main table that stores generated music outputs.
    # We keep the existing table name so the rest of your repo stays compatible.
    op.create_table(
        "music_tracks",

        # Primary key for each stored row
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),

        # Source-system ID; useful for idempotent ingest / deduping source records
        sa.Column("external_id", sa.String(), nullable=False),

        # Searchable text fields
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("acoustic_prompt_descriptive", sa.Text(), nullable=True),

        # Flexible metadata from the source payload.
        # JSONB is used because the DynamoDB-derived input can evolve over time.
        sa.Column(
            "all_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "extra_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),

        # Groups sibling outputs that come from the same generation lineage
        sa.Column("conversion_group_id", postgresql.UUID(as_uuid=True), nullable=False),

        # Position of the sibling inside that lineage group
        sa.Column("conversion_index", sa.Integer(), nullable=False, server_default=sa.text("0")),

        # Vector embedding used for semantic similarity search
        sa.Column("embedding", Vector(1536), nullable=True),

        # Precomputed full-text search column populated by a database trigger
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),

        # Lightweight behavioral counters currently used by your repo
        sa.Column("clicks", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default=sa.text("0")),

        # Real timestamp so freshness/decay logic works correctly later
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),

        # external_id should be unique because it represents the original source record
        sa.UniqueConstraint("external_id", name="uq_music_tracks_external_id"),
    )

    # Trigger function to keep search_vector up to date automatically.
    # Weighting strategy:
    # - title gets highest importance
    # - acoustic_prompt_descriptive gets medium-high importance
    # - all_tags gets lower importance
    op.execute(
        """
        CREATE OR REPLACE FUNCTION music_fts_trigger()
        RETURNS trigger AS $$
        BEGIN
          NEW.search_vector :=
            setweight(to_tsvector('english', coalesce(NEW.title, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(NEW.acoustic_prompt_descriptive, '')), 'B') ||
            setweight(
              to_tsvector(
                'english',
                coalesce(
                  (
                    SELECT string_agg(value, ' ')
                    FROM jsonb_array_elements_text(COALESCE(NEW.all_tags, '[]'::jsonb))
                  ),
                  ''
                )
              ),
              'C'
            );
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
        """
    )

    # Trigger runs before insert/update so search_vector never drifts from source fields
    op.execute(
        """
        CREATE TRIGGER tsvectorupdate
        BEFORE INSERT OR UPDATE ON music_tracks
        FOR EACH ROW
        EXECUTE FUNCTION music_fts_trigger();
        """
    )

    # GIN index for fast Postgres full-text search on search_vector
    op.create_index(
        "ix_fts",
        "music_tracks",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )

    # Trigram GIN index improves fuzzy / typo-tolerant title search
    op.create_index(
        "ix_title_trgm",
        "music_tracks",
        ["title"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )

    # Group lookup index helps dedupe sibling variants from the same generation
    op.create_index(
        "ix_group",
        "music_tracks",
        ["conversion_group_id"],
        unique=False,
    )

    # ANN index for vector similarity search with cosine distance
    # This is much more appropriate than a generic default index for embeddings
    op.execute(
        """
        CREATE INDEX ix_vec
        ON music_tracks
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)
        """
    )


def downgrade():
    # Drop indexes first
    op.execute("DROP INDEX IF EXISTS ix_vec")
    op.drop_index("ix_group", table_name="music_tracks")
    op.drop_index("ix_title_trgm", table_name="music_tracks")
    op.drop_index("ix_fts", table_name="music_tracks")

    # Remove trigger + trigger function
    op.execute("DROP TRIGGER IF EXISTS tsvectorupdate ON music_tracks")
    op.execute("DROP FUNCTION IF EXISTS music_fts_trigger()")

    # Finally drop the table
    op.drop_table("music_tracks")