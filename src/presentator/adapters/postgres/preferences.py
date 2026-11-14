"""The settings and preference tables on Postgres, behind their ports."""

from dataclasses import dataclass
from typing import Final

from sqlalchemy import Engine, text

from presentator.contracts.preferences import (
    InstanceSettings,
    PersonPreferences,
    ThemeChoice,
)

# An instance has one set of settings: the table holds one row and its check
# constraint refuses a second.
_THE_ONLY_ROW: Final = 1
_READ_SETTINGS: Final = text("SELECT name FROM instance_settings WHERE id = :id")
_WRITE_SETTINGS: Final = text(
    "INSERT INTO instance_settings (id, name) VALUES (:id, :name)"
    " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name"
)
_READ_PREFERENCES: Final = text(
    "SELECT theme FROM person_preferences WHERE user_id = :user_id"
)
_WRITE_PREFERENCES: Final = text(
    "INSERT INTO person_preferences (user_id, theme) VALUES (:user_id, :theme)"
    " ON CONFLICT (user_id) DO UPDATE SET theme = EXCLUDED.theme"
)
_DELETE_PREFERENCES: Final = text(
    "DELETE FROM person_preferences WHERE user_id = :user_id"
)


@dataclass(frozen=True, slots=True)
class PostgresInstanceSettingsStore:
    """The instance settings, as one row.

    Writing is one upsert on the primary key, so two processes saving at once
    leave one row holding one of the two names.
    """

    engine: Engine

    def read(self) -> InstanceSettings | None:
        """Read the settings an admin saved."""
        with self.engine.connect() as connection:
            row = connection.execute(_READ_SETTINGS, {"id": _THE_ONLY_ROW}).fetchone()
        return None if row is None else InstanceSettings(name=row[0])

    def write(self, settings: InstanceSettings) -> None:
        """Write the settings over whatever stood there."""
        with self.engine.begin() as connection:
            connection.execute(
                _WRITE_SETTINGS, {"id": _THE_ONLY_ROW, "name": settings.name}
            )


@dataclass(frozen=True, slots=True)
class PostgresPersonPreferencesStore:
    """One row per person who chose a theme.

    Writing is one upsert on the person's primary key, so two processes saving
    one person's choices at once leave one row.
    """

    engine: Engine

    def read(self, user_id: str) -> PersonPreferences | None:
        """Read that person's choices."""
        with self.engine.connect() as connection:
            row = connection.execute(_READ_PREFERENCES, {"user_id": user_id}).fetchone()
        return None if row is None else PersonPreferences(theme=ThemeChoice(row[0]))

    def write(self, user_id: str, preferences: PersonPreferences) -> None:
        """Write that person's choices over whatever stood there."""
        with self.engine.begin() as connection:
            connection.execute(
                _WRITE_PREFERENCES,
                {"user_id": user_id, "theme": preferences.theme.value},
            )

    def delete(self, user_id: str) -> int:
        """Delete that person's choices; how many rows went."""
        with self.engine.begin() as connection:
            return connection.execute(
                _DELETE_PREFERENCES, {"user_id": user_id}
            ).rowcount
