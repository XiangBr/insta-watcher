"""webhook_event and ops_event

Revision ID: 0001
Revises:
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhook_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("received_at", sa.String(27), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.String(27), nullable=True),
    )
    op.create_index(
        "ix_webhook_event_status_received_at", "webhook_event", ["status", "received_at"]
    )
    op.create_table(
        "ops_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("resolved_at", sa.String(27), nullable=True),
    )
    op.create_index("ix_ops_event_created_at", "ops_event", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ops_event_created_at", table_name="ops_event")
    op.drop_table("ops_event")
    op.drop_index("ix_webhook_event_status_received_at", table_name="webhook_event")
    op.drop_table("webhook_event")
