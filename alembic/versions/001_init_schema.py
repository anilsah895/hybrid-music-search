from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "001_init_schema"
down_revision = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    op.create_table(
        "music_tracks",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column("external_id", sa.String),
        sa.Column("title", sa.Text),
        sa.Column("acoustic_prompt_descriptive", sa.Text),

        sa.Column("all_tags", sa.dialects.postgresql.JSONB),
        sa.Column("extra_metadata", sa.dialects.postgresql.JSONB),
        sa.Column("raw_payload", sa.dialects.postgresql.JSONB),

        sa.Column("conversion_group_id", sa.UUID),
        sa.Column("conversion_index", sa.Integer),

        sa.Column("embedding", Vector(1536)),
        sa.Column("search_vector", sa.dialects.postgresql.TSVECTOR),

        sa.Column("clicks", sa.Integer, default=0),
        sa.Column("impressions", sa.Integer, default=0),
        sa.Column("created_at", sa.Integer),
    )

    # FIXED: FTS trigger
    op.execute("""
        CREATE FUNCTION music_fts_trigger() RETURNS trigger AS $$
        begin
        new.search_vector :=
            setweight(
                to_tsvector('english', coalesce(new.title,'')),
                'A'
            ) ||
            setweight(
                to_tsvector('english', coalesce(new.acoustic_prompt_descriptive,'')),
                'B'
            ) ||
            setweight(
                to_tsvector(
                    'english',
                    coalesce(
                        (
                            SELECT string_agg(value, ' ')
                            FROM jsonb_array_elements_text(new.all_tags)
                        ),
                        ''
                    )
                ),
                'C'
            );

        return new;
        end
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER tsvectorupdate
    BEFORE INSERT OR UPDATE
    ON music_tracks
    FOR EACH ROW EXECUTE FUNCTION music_fts_trigger();
    """)


def downgrade():
    op.drop_table("music_tracks")