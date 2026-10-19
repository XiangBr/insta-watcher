"""follow gate: campaign options, delivery state, job payload, postback receipts

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-02 23:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ADD COLUMN everywhere, never batch_alter_table: a batch recreates the table, and
    # dropping `campaign` or `delivery` fails while other rows reference them (foreign keys
    # are on), which would keep the container from booting. See migration 0004.
    op.add_column(
        "campaign",
        sa.Column("follow_gate_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("campaign", sa.Column("follow_gate_message", sa.Text(), nullable=True))
    op.add_column(
        "campaign", sa.Column("follow_gate_button_label", sa.String(length=20), nullable=True)
    )

    op.add_column("delivery", sa.Column("gate_state", sa.String(length=16), nullable=True))
    op.add_column(
        "delivery",
        sa.Column("gate_taps", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("delivery", sa.Column("gate_user_id", sa.String(length=64), nullable=True))
    op.add_column("delivery", sa.Column("gate_tapped_at", sa.String(27), nullable=True))
    op.add_column("delivery", sa.Column("followup_status", sa.String(length=16), nullable=True))
    op.add_column("delivery", sa.Column("followup_sent_at", sa.String(27), nullable=True))
    op.add_column(
        "delivery", sa.Column("followup_message_id", sa.String(length=128), nullable=True)
    )
    op.add_column("delivery", sa.Column("followup_error", sa.Text(), nullable=True))

    op.add_column("job", sa.Column("payload", sa.JSON(), nullable=True))

    op.create_table(
        "postback_receipt",
        sa.Column("mid", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("mid"),
    )
    op.create_index(
        op.f("ix_postback_receipt_created_at"), "postback_receipt", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_postback_receipt_created_at"), table_name="postback_receipt")
    op.drop_table("postback_receipt")
    op.drop_column("job", "payload")
    for column in (
        "followup_error",
        "followup_message_id",
        "followup_sent_at",
        "followup_status",
        "gate_tapped_at",
        "gate_user_id",
        "gate_taps",
        "gate_state",
    ):
        op.drop_column("delivery", column)
    op.drop_column("campaign", "follow_gate_button_label")
    op.drop_column("campaign", "follow_gate_message")
    op.drop_column("campaign", "follow_gate_enabled")
