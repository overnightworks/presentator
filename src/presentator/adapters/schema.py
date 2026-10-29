"""The one place the whole database schema is made to exist."""

from pathlib import Path
from typing import Final

from presentator.adapters.decks import create_deck_tables
from presentator.adapters.identity import create_identity_tables
from presentator.adapters.preferences import create_preference_tables
from presentator.adapters.sqlite import immediate_transaction
from presentator.contracts.decks import BuildFailureReason

_DECK_COLUMNS: Final = "SELECT name FROM pragma_table_info('decks')"
_ADD_COLUMNS: Final = (
    "ALTER TABLE decks ADD COLUMN attempt_reason TEXT",
    "ALTER TABLE decks ADD COLUMN attempt_written_megabytes INTEGER",
    "ALTER TABLE decks ADD COLUMN attempt_allowed_megabytes INTEGER",
    "ALTER TABLE decks ADD COLUMN attempt_allowed_entries INTEGER",
)
# Only the toolchain's words and the absence of any were ever stored before the
# reason: a failed attempt with text is `toolchain`, one without is `no-words`.
_BACKFILL_REASON: Final = """
UPDATE decks
SET attempt_reason = CASE
    WHEN attempt_failure IS NULL OR attempt_failure = '' THEN ? ELSE ?
END
WHERE attempt_outcome = 'failed'
"""


def create_tables(database: Path) -> None:
    """Make every table exist; the identity step refuses an old database first."""
    create_identity_tables(database)
    create_preference_tables(database)
    create_deck_tables(database)
    _add_the_failure_reason(database)


def _add_the_failure_reason(database: Path) -> None:
    """Give a deck table from before the reason its columns, in one transaction.

    Nothing is lost and no old column changes; every deck statement names its
    columns, so a release from before this still reads the upgraded file.
    """
    with immediate_transaction(database) as cursor:
        present = {name for (name,) in cursor.execute(_DECK_COLUMNS)}
        if "attempt_reason" in present:
            return
        for statement in _ADD_COLUMNS:
            cursor.execute(statement)
        cursor.execute(
            _BACKFILL_REASON,
            (BuildFailureReason.NO_WORDS.value, BuildFailureReason.TOOLCHAIN.value),
        )
