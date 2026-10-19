"""campaign triggers: comments and story replies

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-02 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ADD COLUMN on purpose, not batch_alter_table. A batch recreates the table, and
    # dropping `campaign` fails while `delivery` rows reference it (foreign keys are on), which
    # would leave the container unable to boot. SQLite adds a NOT NULL column in place when it
    # has a default. Existing campaigns keep answering comments and not story replies.
    op.add_column(
        "campaign",
        sa.Column("trigger_comments", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "campaign",
        sa.Column("trigger_story_replies", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("campaign", "trigger_story_replies")
    op.drop_column("campaign", "trigger_comments")
