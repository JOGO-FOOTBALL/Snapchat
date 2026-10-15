"""move publish_log tables to crosspost schema, platform-neutral names

Revision ID: c3f1a8d2e6b4
Revises: ab2519737295
Create Date: 2026-10-15 12:00:00.000000

Moves snapchat.publish_log and snapchat.publish_log_snaps into the new
crosspost schema (in place, no data copy), renames publish_log_snaps to
publish_log_items, makes the Snapchat-specific column names platform-neutral,
adds a `platform` column (backfilled from destination) and an `extra` JSONB
column for platform-specific fields.

alembic_version stays in the snapchat schema (see version_table_schema in
alembic/env.py).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c3f1a8d2e6b4'
down_revision: Union[str, Sequence[str], None] = 'ab2519737295'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SCHEMA IF NOT EXISTS crosspost")

    # Move tables; owned id sequences, indexes and constraints move with them.
    op.execute("ALTER TABLE snapchat.publish_log SET SCHEMA crosspost")
    op.execute("ALTER TABLE snapchat.publish_log_snaps SET SCHEMA crosspost")

    # publish_log
    op.alter_column('publish_log', 'snapchat_media_id', new_column_name='external_media_id', schema='crosspost')
    op.alter_column('publish_log', 'snapchat_request_id', new_column_name='external_request_id', schema='crosspost')
    op.add_column('publish_log', sa.Column('extra', postgresql.JSONB(), nullable=True), schema='crosspost')
    op.add_column('publish_log', sa.Column('platform', sa.String(), nullable=True), schema='crosspost')
    op.execute(
        "UPDATE crosspost.publish_log SET platform = CASE "
        "WHEN destination IN ('story', 'spotlight') THEN 'snapchat' "
        "ELSE destination END"
    )
    op.alter_column('publish_log', 'platform', nullable=False, schema='crosspost')
    op.create_index(op.f('ix_crosspost_publish_log_platform'), 'publish_log', ['platform'], unique=False, schema='crosspost')
    op.execute("ALTER INDEX crosspost.ix_snapchat_publish_log_ig_content_id RENAME TO ix_crosspost_publish_log_ig_content_id")

    # publish_log_snaps -> publish_log_items
    op.rename_table('publish_log_snaps', 'publish_log_items', schema='crosspost')
    op.alter_column('publish_log_items', 'snap_index', new_column_name='item_index', schema='crosspost')
    op.alter_column('publish_log_items', 'snapchat_media_id', new_column_name='external_media_id', schema='crosspost')
    op.alter_column('publish_log_items', 'snapchat_request_id', new_column_name='external_request_id', schema='crosspost')
    op.execute("ALTER SEQUENCE crosspost.publish_log_snaps_id_seq RENAME TO publish_log_items_id_seq")
    op.execute("ALTER TABLE crosspost.publish_log_items RENAME CONSTRAINT publish_log_snaps_pkey TO publish_log_items_pkey")
    op.execute(
        "ALTER TABLE crosspost.publish_log_items "
        "RENAME CONSTRAINT publish_log_snaps_publish_log_id_fkey TO publish_log_items_publish_log_id_fkey"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "ALTER TABLE crosspost.publish_log_items "
        "RENAME CONSTRAINT publish_log_items_publish_log_id_fkey TO publish_log_snaps_publish_log_id_fkey"
    )
    op.execute("ALTER TABLE crosspost.publish_log_items RENAME CONSTRAINT publish_log_items_pkey TO publish_log_snaps_pkey")
    op.execute("ALTER SEQUENCE crosspost.publish_log_items_id_seq RENAME TO publish_log_snaps_id_seq")
    op.alter_column('publish_log_items', 'external_request_id', new_column_name='snapchat_request_id', schema='crosspost')
    op.alter_column('publish_log_items', 'external_media_id', new_column_name='snapchat_media_id', schema='crosspost')
    op.alter_column('publish_log_items', 'item_index', new_column_name='snap_index', schema='crosspost')
    op.rename_table('publish_log_items', 'publish_log_snaps', schema='crosspost')

    op.execute("ALTER INDEX crosspost.ix_crosspost_publish_log_ig_content_id RENAME TO ix_snapchat_publish_log_ig_content_id")
    # IF EXISTS: dev ran an earlier draft of this revision without platform.
    op.execute("DROP INDEX IF EXISTS crosspost.ix_crosspost_publish_log_platform")
    op.execute("ALTER TABLE crosspost.publish_log DROP COLUMN IF EXISTS platform")
    op.drop_column('publish_log', 'extra', schema='crosspost')
    op.alter_column('publish_log', 'external_request_id', new_column_name='snapchat_request_id', schema='crosspost')
    op.alter_column('publish_log', 'external_media_id', new_column_name='snapchat_media_id', schema='crosspost')

    op.execute("ALTER TABLE crosspost.publish_log_snaps SET SCHEMA snapchat")
    op.execute("ALTER TABLE crosspost.publish_log SET SCHEMA snapchat")
    op.execute("DROP SCHEMA IF EXISTS crosspost")
