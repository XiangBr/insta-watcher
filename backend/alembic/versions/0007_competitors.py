"""competitors: watched profiles, their posts, Apify runs and model reports

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-19 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # New tables only: nothing existing is touched.
    op.create_table(
        "competitor",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("is_self", sa.Boolean(), nullable=False),
        sa.Column("added_at", sa.String(27), nullable=False),
        sa.Column("full_name", sa.String(length=256), nullable=True),
        sa.Column("biography", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("external_url", sa.String(length=2048), nullable=True),
        sa.Column("profile_pic_url", sa.String(length=2048), nullable=True),
        sa.Column("followers", sa.Integer(), nullable=True),
        sa.Column("follows", sa.Integer(), nullable=True),
        sa.Column("posts_count", sa.Integer(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=True),
        sa.Column("private", sa.Boolean(), nullable=True),
        sa.Column("scraped_at", sa.String(27), nullable=True),
        sa.Column("scrape_error", sa.Text(), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("labels_version", sa.String(length=16), nullable=True),
        sa.Column("classify_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "competitor_post",
        sa.Column("post_id", sa.String(length=64), nullable=False),
        sa.Column("competitor_id", sa.Integer(), nullable=False),
        sa.Column("short_code", sa.String(length=32), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("media_type", sa.String(length=16), nullable=True),
        sa.Column("product_type", sa.String(length=16), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("hashtags", sa.JSON(), nullable=False),
        sa.Column("posted_at", sa.String(27), nullable=False),
        sa.Column("likes", sa.Integer(), nullable=True),
        sa.Column("comments", sa.Integer(), nullable=True),
        sa.Column("views", sa.Integer(), nullable=True),
        sa.Column("display_url", sa.String(length=2048), nullable=True),
        sa.Column("is_pinned", sa.Boolean(), nullable=False),
        sa.Column("scraped_at", sa.String(27), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("labels_version", sa.String(length=16), nullable=True),
        sa.Column("classify_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["competitor_id"], ["competitor.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("post_id"),
    )
    op.create_index(
        "ix_competitor_post_competitor_posted",
        "competitor_post",
        ["competitor_id", "posted_at"],
        unique=False,
    )
    op.create_table(
        "competitor_scan",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("apify_run_id", sa.String(length=64), nullable=True),
        sa.Column("dataset_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("usernames", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.String(27), nullable=False),
        sa.Column("finished_at", sa.String(27), nullable=True),
        sa.Column("profiles", sa.Integer(), nullable=False),
        sa.Column("posts", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "competitor_report",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("finished_at", sa.String(27), nullable=True),
        sa.Column("content", sa.JSON(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("competitor_report")
    op.drop_table("competitor_scan")
    op.drop_index("ix_competitor_post_competitor_posted", table_name="competitor_post")
    op.drop_table("competitor_post")
    op.drop_table("competitor")
