"""The ssh host keys every process trusts, one per host."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    """Add the table; the host is the key, so a host is pinned once."""
    op.create_table(
        "host_key_pins",
        sa.Column("host", sa.Text(), primary_key=True),
        sa.Column("public_key", sa.Text(), nullable=False),
    )
