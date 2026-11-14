"""A migration history one revision longer than the release's, for an older database."""

import shutil
from pathlib import Path
from typing import Final

from presentator.adapters.postgres.migrate import MIGRATIONS_DIRECTORY

_NEXT_REVISION: Final = '''"""A revision a database made by an older release lacks."""

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    """Add nothing."""
'''


def a_longer_history(directory: Path) -> Path:
    """A copy of the real history with one more revision on top of its head."""
    history = directory / "migrations"
    shutil.copytree(
        MIGRATIONS_DIRECTORY, history, ignore=shutil.ignore_patterns("__pycache__")
    )
    (history / "versions" / "0002_next.py").write_text(_NEXT_REVISION, encoding="utf-8")
    return history
