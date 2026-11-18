"""A migration history one revision longer than the release's, for an older database."""

import shutil
from pathlib import Path
from typing import Final

from presentator.adapters.postgres.migrate import (
    MIGRATIONS_DIRECTORY,
    migration_history,
)

_NEXT_REVISION: Final = "next"


def _next_revision(head: str) -> str:
    return f'''"""A revision a database made by an older release lacks."""

revision = "{_NEXT_REVISION}"
down_revision = "{head}"


def upgrade() -> None:
    """Add nothing."""
'''


def a_longer_history(directory: Path) -> Path:
    """A copy of the real history with one more revision on top of its head."""
    history = directory / "migrations"
    shutil.copytree(
        MIGRATIONS_DIRECTORY, history, ignore=shutil.ignore_patterns("__pycache__")
    )
    head = migration_history().get_current_head()
    assert head is not None
    (history / "versions" / f"{_NEXT_REVISION}.py").write_text(
        _next_revision(head), encoding="utf-8"
    )
    return history
