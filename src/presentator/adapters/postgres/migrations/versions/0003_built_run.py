"""The run a deck's build is kept as in the object store."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    """Add the column; a row without it names no build any process can serve."""
    op.add_column("decks", sa.Column("built_run", sa.Text()))
