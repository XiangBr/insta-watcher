"""chat: contacts, messages, flows, outbox, settings, and the direct-message campaign trigger

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-19 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ADD COLUMN, never batch_alter_table (see 0005).
    op.add_column(
        "campaign",
        sa.Column("trigger_messages", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "setting",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "contact",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ig_user_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_seen_at", sa.String(27), nullable=False),
        sa.Column("last_inbound_at", sa.String(27), nullable=True),
        sa.Column("last_outbound_at", sa.String(27), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("human_until", sa.String(27), nullable=True),
        sa.Column("human_reason", sa.String(length=32), nullable=True),
        sa.Column("messages_in", sa.Integer(), nullable=False),
        sa.Column("messages_out", sa.Integer(), nullable=False),
        sa.Column("anonymized_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ig_user_id"),
    )
    op.create_index("ix_contact_last_inbound_at", "contact", ["last_inbound_at"], unique=False)
    op.create_table(
        "chat_message",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=4), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("mid", sa.String(length=256), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("payload", sa.String(length=256), nullable=True),
        sa.Column("flow_id", sa.Integer(), nullable=True),
        sa.Column("step_id", sa.String(length=32), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.ForeignKeyConstraint(["contact_id"], ["contact.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mid"),
    )
    op.create_index("ix_chat_message_contact_created", "chat_message", ["contact_id", "created_at"])
    op.create_index(op.f("ix_chat_message_created_at"), "chat_message", ["created_at"])
    op.create_table(
        "flow",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("match_mode", sa.String(length=16), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.Column("deleted_at", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "chat_outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("run_at", sa.String(27), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.String(27), nullable=True),
        sa.Column("send_started_at", sa.String(27), nullable=True),
        sa.Column("sent_at", sa.String(27), nullable=True),
        sa.Column("message_id", sa.String(length=256), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
        sa.ForeignKeyConstraint(["contact_id"], ["contact.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_chat_outbox_status_run_at", "chat_outbox", ["status", "run_at"])
    op.create_index("ix_chat_outbox_contact", "chat_outbox", ["contact_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_outbox_contact", table_name="chat_outbox")
    op.drop_index("ix_chat_outbox_status_run_at", table_name="chat_outbox")
    op.drop_table("chat_outbox")
    op.drop_table("flow")
    op.drop_index(op.f("ix_chat_message_created_at"), table_name="chat_message")
    op.drop_index("ix_chat_message_contact_created", table_name="chat_message")
    op.drop_table("chat_message")
    op.drop_index("ix_contact_last_inbound_at", table_name="contact")
    op.drop_table("contact")
    op.drop_table("setting")
    op.drop_column("campaign", "trigger_messages")
