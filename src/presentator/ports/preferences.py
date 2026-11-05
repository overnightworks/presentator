"""The stores Settings and the person menu's theme read and write."""

from abc import abstractmethod
from typing import Protocol

from presentator.contracts.preferences import InstanceSettings, PersonPreferences


class InstanceSettingsStore(Protocol):
    """The one row an admin edits under Settings."""

    @abstractmethod
    def read(self) -> InstanceSettings | None:
        """Return what an admin saved, or nothing while nobody has."""

    @abstractmethod
    def write(self, settings: InstanceSettings) -> None:
        """Keep the instance defaults, replacing whatever stood there."""


class PersonPreferencesStore(Protocol):
    """The choices that belong beside one account."""

    @abstractmethod
    def read(self, user_id: str) -> PersonPreferences | None:
        """Return that person's choices, or nothing while they have no row."""

    @abstractmethod
    def write(self, user_id: str, preferences: PersonPreferences) -> None:
        """Keep that person's choices, replacing whatever stood there."""

    @abstractmethod
    def delete(self, user_id: str) -> int:
        """Delete that person's choices; how many rows went."""
