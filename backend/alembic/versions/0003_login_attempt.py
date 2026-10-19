"""login_attempt: dashboard login throttle

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-02 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login_attempt",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("last_failure_at", sa.String(27), nullable=False),
        sa.Column("blocked_until", sa.String(27), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )
    with op.batch_alter_table("login_attempt", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_login_attempt_last_failure_at"), ["last_failure_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("login_attempt", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_login_attempt_last_failure_at"))
    op.drop_table("login_attempt")
