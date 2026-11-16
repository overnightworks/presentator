"""The epoch that fences the refresh and the removal writes of a holder."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    """Add the one-row table; a migration only adds, so there is no downgrade."""
    refresh_owner = op.create_table(
        "refresh_owner",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("id = 1"),
    )
    op.bulk_insert(refresh_owner, [{"id": 1, "epoch": 0}])
