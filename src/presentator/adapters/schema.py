"""The one place the whole database schema is made to exist."""

from pathlib import Path
from typing import Final

from presentator.adapters.decks import create_deck_tables
from presentator.adapters.identity import create_identity_tables
from presentator.adapters.preferences import create_preference_tables
from presentator.adapters.sqlite import immediate_transaction
from presentator.contracts.decks import BuildFailureReason

_DECK_COLUMNS: Final = "SELECT name FROM pragma_table_info('decks')"
_ADD_COLUMNS: Final = {
    "attempt_reason": "ALTER TABLE decks ADD COLUMN attempt_reason TEXT",
    "attempt_written_megabytes": (
        "ALTER TABLE decks ADD COLUMN attempt_written_megabytes INTEGER"
    ),
    "attempt_allowed_megabytes": (
        "ALTER TABLE decks ADD COLUMN attempt_allowed_megabytes INTEGER"
    ),
    "attempt_allowed_entries": (
        "ALTER TABLE decks ADD COLUMN attempt_allowed_entries INTEGER"
    ),
}
# What a release from before the reason, or one that does not know it, leaves
# beside the reason: whether a failure carries words is `BuildFailure.said`'s
# rule, so an empty text is no text and a reason follows from the text.
_EMPTY_TEXT_IS_NONE: Final = """
UPDATE decks SET attempt_failure = NULL WHERE attempt_failure = ''
"""
_ONLY_A_FAILURE_HAS_A_REASON: Final = """
UPDATE decks
SET attempt_reason = NULL, attempt_written_megabytes = NULL,
    attempt_allowed_megabytes = NULL, attempt_allowed_entries = NULL
WHERE attempt_outcome IS NOT 'failed' AND (
    attempt_reason IS NOT NULL OR attempt_written_megabytes IS NOT NULL
    OR attempt_allowed_megabytes IS NOT NULL OR attempt_allowed_entries IS NOT NULL
)
"""
# A server reason never has text, and `toolchain` always has; a failed row that
# disagrees, or has no reason, is given the reason its text names and no counts.
_THE_REASON_FOLLOWS_THE_TEXT: Final = """
UPDATE decks
SET attempt_reason = CASE WHEN attempt_failure IS NULL THEN ? ELSE ? END,
    attempt_written_megabytes = NULL, attempt_allowed_megabytes = NULL,
    attempt_allowed_entries = NULL
WHERE attempt_outcome = 'failed' AND (
    attempt_reason IS NULL
    OR (attempt_failure IS NOT NULL AND attempt_reason != ?)
    OR (attempt_failure IS NULL AND attempt_reason = ?)
)
"""


def create_tables(database: Path) -> None:
    """Make every table exist; the identity step refuses an old database first."""
    create_identity_tables(database)
    create_preference_tables(database)
    create_deck_tables(database)
    _keep_the_failure_reason(database)


def _keep_the_failure_reason(database: Path) -> None:
    """Give the deck table the reason's columns and make its rows agree, on every start.

    One immediate transaction. Nothing is lost and no old column changes; every
    deck statement names its columns, so a release from before this still reads
    the file. What such a release writes into it afterwards is made to agree
    again here, so going forward after a rollback reads every row.
    """
    toolchain = BuildFailureReason.TOOLCHAIN.value
    with immediate_transaction(database) as cursor:
        present = {name for (name,) in cursor.execute(_DECK_COLUMNS)}
        for name, statement in _ADD_COLUMNS.items():
            if name not in present:
                cursor.execute(statement)
        cursor.execute(_EMPTY_TEXT_IS_NONE)
        cursor.execute(_ONLY_A_FAILURE_HAS_A_REASON)
        cursor.execute(
            _THE_REASON_FOLLOWS_THE_TEXT,
            (BuildFailureReason.NO_WORDS.value, toolchain, toolchain, toolchain),
        )
