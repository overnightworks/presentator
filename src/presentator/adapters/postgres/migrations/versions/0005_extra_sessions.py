"""The sessions table in the shape of webauth's Postgres extra.

One-way: the table is dropped and made again, so every session ends once, and
the release before reads `sessions.id` and `sessions.user_id`, which no longer
exist. That release fails every session read and every sign-in until it rolls
forward or its table shape is restored by hand. This is the one revision that
drops what the release before still reads in the same release.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    """Replace the table; every session ends, and none lasts over twelve hours."""
    op.drop_table("sessions")
    op.create_table(
        "sessions",
        sa.Column("id_hash", sa.Text(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("refresh_token", sa.Text()),
        sa.Column("id_token", sa.Text()),
        sa.Column("account_language", sa.Text()),
    )
    op.create_index(
        "sessions_account_created_at", "sessions", ["account_id", "created_at"]
    )
    op.create_index("sessions_expires_at", "sessions", ["expires_at"])
