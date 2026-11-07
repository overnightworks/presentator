"""The one place the whole database schema is made to exist."""

from pathlib import Path
from typing import Final

from presentator.adapters.decks import create_deck_tables
from presentator.adapters.identity import KIND_COLUMN, create_identity_tables
from presentator.adapters.preferences import create_preference_tables
from presentator.adapters.sqlite import immediate_transaction
from presentator.contracts.preferences import ThemeChoice

_INSTANCE_COLUMNS: Final = "SELECT name FROM pragma_table_info('instance_settings')"
_THE_INSTANCE_THEME: Final = "theme"
_THE_ACCOUNT_KIND: Final = "kind"
_ACCOUNT_COLUMNS: Final = "SELECT name FROM pragma_table_info('users')"
_UNCHOSEN_THEME_FOLLOWS_THE_SYSTEM: Final = (
    "UPDATE person_preferences SET theme = ? WHERE theme IS NULL"
)


def create_tables(database: Path) -> None:
    """Make every table exist, then run the account-kind and the two theme upgrades."""
    create_identity_tables(database)
    create_preference_tables(database)
    create_deck_tables(database)
    _every_account_has_a_kind(database)
    _unchosen_theme_follows_the_system(database)
    _retire_the_instance_theme(database)


def _every_account_has_a_kind(database: Path) -> None:
    """Give the accounts table its kind column on every start; every row reads person.

    One immediate transaction, so two starts on one file cannot both add it. The
    column defaults to person, so the release before, inserting without it after a
    rollback, still writes a valid row.
    """
    with immediate_transaction(database) as cursor:
        present = {name for (name,) in cursor.execute(_ACCOUNT_COLUMNS)}
        if _THE_ACCOUNT_KIND not in present:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {KIND_COLUMN}")


def _unchosen_theme_follows_the_system(database: Path) -> None:
    """Give a person row that held no theme the value that means none, on every start.

    No row goes and no stored choice changes. The release before still reads the
    file; what is lost is which people had no choice. A `NULL` that release
    writes afterwards is folded again at the next start, so going forward after
    a rollback reads every row.
    """
    with immediate_transaction(database) as cursor:
        cursor.execute(
            _UNCHOSEN_THEME_FOLLOWS_THE_SYSTEM, (ThemeChoice.FOLLOW_SYSTEM.value,)
        )


def _retire_the_instance_theme(database: Path) -> None:
    """Drop the instance's unread theme column, on every start; it is one-way.

    One immediate transaction, so two starts on one file cannot both drop. No row
    goes. Code that still names the column reads the file and updates its row, but
    cannot insert a first settings row into it.
    """
    with immediate_transaction(database) as cursor:
        present = {name for (name,) in cursor.execute(_INSTANCE_COLUMNS)}
        if _THE_INSTANCE_THEME in present:
            cursor.execute(
                f"ALTER TABLE instance_settings DROP COLUMN {_THE_INSTANCE_THEME}"
            )
