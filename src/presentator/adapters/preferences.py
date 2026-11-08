"""SQLite rows behind the settings and preference ports."""

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from presentator.adapters.sqlite import apply_schema, immediate_transaction, rows
from presentator.contracts.preferences import (
    InstanceSettings,
    PersonPreferences,
    ThemeChoice,
)

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS instance_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL,
    language_tag TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS person_preferences (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    theme TEXT
);
"""
# An instance has one set of settings, so the table holds one row and the
# schema refuses a second.
_THE_ONLY_ROW: Final = 1


def create_preference_tables(database: Path) -> None:
    """Make the settings and preference schema exist."""
    apply_schema(database, _SCHEMA)


@dataclass(frozen=True, slots=True)
class SqliteInstanceSettingsStore:
    """The instance settings, as one row."""

    database: Path

    def read(self) -> InstanceSettings | None:
        """Read the settings an admin saved."""
        with rows(self.database) as cursor:
            row = cursor.execute(
                "SELECT name, language_tag FROM instance_settings WHERE id = ?",
                (_THE_ONLY_ROW,),
            ).fetchone()
        if row is None:
            return None
        name, language_tag = row
        return InstanceSettings(name=name, language_tag=language_tag)

    def write(self, settings: InstanceSettings) -> None:
        """Write the settings over whatever stood there."""
        with immediate_transaction(self.database) as cursor:
            cursor.execute(
                "INSERT INTO instance_settings (id, name, language_tag)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " name = excluded.name, language_tag = excluded.language_tag",
                (_THE_ONLY_ROW, settings.name, settings.language_tag),
            )


@dataclass(frozen=True, slots=True)
class SqlitePersonPreferencesStore:
    """One row per person who chose a theme."""

    database: Path

    def read(self, user_id: str) -> PersonPreferences | None:
        """Read that person's choices."""
        with rows(self.database) as cursor:
            row = cursor.execute(
                "SELECT theme FROM person_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        (theme,) = row
        return PersonPreferences(theme=ThemeChoice(theme))

    def write(self, user_id: str, preferences: PersonPreferences) -> None:
        """Write that person's choices over whatever stood there."""
        with rows(self.database) as cursor:
            cursor.execute(
                "INSERT INTO person_preferences (user_id, theme) VALUES (?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET theme = excluded.theme",
                (user_id, preferences.theme.value),
            )

    def delete(self, user_id: str) -> int:
        """Delete that person's choices; how many rows went."""
        with rows(self.database) as cursor:
            cursor.execute(
                "DELETE FROM person_preferences WHERE user_id = ?", (user_id,)
            )
            return int(cursor.rowcount)
