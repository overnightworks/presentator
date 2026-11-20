"""Drop the decks columns the build run replaced.

One-way: `built_run` names every build, and the old values name builds no
process serves. The release before reads neither column, so it runs on.
"""

from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade() -> None:
    """Drop both columns under an exclusive table lock.

    The lock waits for every open transaction on `decks` to end, and statements of
    the release before queue behind it until this transaction commits; one that
    queues past the statement timeout fails.
    """
    op.drop_column("decks", "active_build")
    op.drop_column("decks", "pdf_export")
