"""The build paths the object store replaced, one release after their last reader."""

from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    """Drop both columns, with no downgrade: `built_run` names every build."""
    op.drop_column("decks", "active_build")
    op.drop_column("decks", "pdf_export")
