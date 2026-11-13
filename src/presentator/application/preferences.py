"""The instance's settings, a person's choices, and how a page resolves them.

The instance has a name. The language a page reads in is the account's or the
browser's, which the api resolves from the offered languages. The theme is the
person's own and, with none chosen, follows the device.
"""

from dataclasses import dataclass

from presentator.contracts.preferences import (
    Appearance,
    InstanceSettings,
    PersonPreferences,
)
from presentator.contracts.words import LANGUAGES, SOURCE_LANGUAGE, Language
from presentator.ports.preferences import (
    InstanceSettingsStore,
    PersonPreferencesStore,
)


def offered_language(tag: str) -> Language:
    """The offered language a stored or resolved tag names, else the fallback."""
    return tag if tag in LANGUAGES else SOURCE_LANGUAGE


@dataclass(frozen=True, slots=True, kw_only=True)
class Preferences:
    """Every use case the lobby has around language and theme."""

    instance: InstanceSettingsStore
    people: PersonPreferencesStore

    def instance_settings(self) -> InstanceSettings:
        """What an admin set, or what a fresh instance starts with."""
        return self.instance.read() or InstanceSettings()

    def save_instance_settings(self, settings: InstanceSettings) -> None:
        """Keep the settings every person reads."""
        self.instance.write(settings)

    def preferences_of(self, user_id: str) -> PersonPreferences:
        """What that person chose, or follow-system while they chose nothing."""
        return self.people.read(user_id) or PersonPreferences()

    def save_preferences_of(self, user_id: str, chosen: PersonPreferences) -> None:
        """Keep that person's choice; the instance settings stay untouched."""
        self.people.write(user_id, chosen)

    def appearance_for(self, user_id: str | None, *, language: Language) -> Appearance:
        """The given language and the person's own theme."""
        chosen = (
            PersonPreferences() if user_id is None else self.preferences_of(user_id)
        )
        return Appearance(language=language, theme=chosen.theme)
