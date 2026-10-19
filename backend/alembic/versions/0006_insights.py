"""insights: media metrics, daily account metrics, snapshots, comment activity

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-12 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # New tables only: nothing existing is touched.
    op.create_table(
        "insight_media",
        sa.Column("media_id", sa.String(length=32), nullable=False),
        sa.Column("posted_at", sa.String(27), nullable=False),
        sa.Column("media_type", sa.String(length=24), nullable=False),
        sa.Column("product_type", sa.String(length=16), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("permalink", sa.String(length=2048), nullable=True),
        sa.Column("thumbnail_url", sa.String(length=2048), nullable=True),
        sa.Column("media_url", sa.String(length=2048), nullable=True),
        sa.Column("like_count", sa.Integer(), nullable=True),
        sa.Column("comments_count", sa.Integer(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("terms", sa.JSON(), nullable=False),
        sa.Column("comments_seen_count", sa.Integer(), nullable=True),
        sa.Column("insights_fetched_at", sa.String(27), nullable=True),
        sa.Column("insights_error", sa.Text(), nullable=True),
        sa.Column("listed_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("media_id"),
    )
    op.create_index("ix_insight_media_posted_at", "insight_media", ["posted_at"], unique=False)
    op.create_table(
        "insight_day",
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("followers_count", sa.Integer(), nullable=True),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("fetched_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("day"),
    )
    op.create_table(
        "insight_snapshot",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "insight_comment",
        sa.Column("comment_id", sa.String(length=64), nullable=False),
        sa.Column("media_id", sa.String(length=32), nullable=False),
        sa.Column("commented_at", sa.String(27), nullable=False),
        sa.Column("author_id", sa.String(length=64), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("comment_id"),
    )
    op.create_index(
        "ix_insight_comment_commented_at", "insight_comment", ["commented_at"], unique=False
    )
    op.create_index(
        op.f("ix_insight_comment_media_id"), "insight_comment", ["media_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_insight_comment_media_id"), table_name="insight_comment")
    op.drop_index("ix_insight_comment_commented_at", table_name="insight_comment")
    op.drop_table("insight_comment")
    op.drop_table("insight_snapshot")
    op.drop_table("insight_day")
    op.drop_index("ix_insight_media_posted_at", table_name="insight_media")
    op.drop_table("insight_media")
