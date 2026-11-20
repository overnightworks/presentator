"""Drop the decks columns the build run replaced.

One-way: `built_run` names every build, and the old values name builds no
process serves. The release before reads neither column, so it runs on.
"""

from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade() -> None:
    """Drop both columns; the table lock waits only for statements already open."""
    op.drop_column("decks", "active_build")
    op.drop_column("decks", "pdf_export")
