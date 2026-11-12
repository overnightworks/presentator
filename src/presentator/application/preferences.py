"""The instance's settings, a person's choices, and how a page resolves them.

The instance names the language every page falls back to; a signed-in person's
account may name another that a catalog speaks, which the api resolves from the
offered languages. The theme is the person's own and, with none chosen, follows the
device.
"""

from dataclasses import dataclass

from presentator.contracts.preferences import (
    DEFAULT_LANGUAGE,
    Appearance,
    InstanceSettings,
    PersonPreferences,
)
from presentator.contracts.words import LANGUAGES, Language
from presentator.ports.preferences import (
    InstanceSettingsStore,
    PersonPreferencesStore,
)


def offered_language(tag: str) -> Language:
    """The offered language a stored or resolved tag names, else the fallback."""
    return tag if tag in LANGUAGES else DEFAULT_LANGUAGE


class UnspokenLanguageError(Exception):
    """A language was chosen that no catalog speaks."""

    def __init__(self, language_tag: str) -> None:
        """Remember the refused tag."""
        super().__init__(language_tag)
        self.language_tag = language_tag


@dataclass(frozen=True, slots=True, kw_only=True)
class Preferences:
    """Every use case the lobby has around language and theme."""

    instance: InstanceSettingsStore
    people: PersonPreferencesStore

    def instance_settings(self) -> InstanceSettings:
        """What an admin set, or what a fresh instance starts with."""
        return self.instance.read() or InstanceSettings()

    def save_instance_settings(self, settings: InstanceSettings) -> None:
        """Keep the settings every person reads.

        Raises `UnspokenLanguageError` for a language newly chosen that no catalog
        speaks; the one already stored stays, so a name change survives a
        removed catalog.
        """
        self._require_newly_chosen_spoken(
            settings.language_tag, stored=self.instance_settings().language_tag
        )
        self.instance.write(settings)

    def preferences_of(self, user_id: str) -> PersonPreferences:
        """What that person chose, or follow-system while they chose nothing."""
        return self.people.read(user_id) or PersonPreferences()

    def save_preferences_of(self, user_id: str, chosen: PersonPreferences) -> None:
        """Keep that person's choice; the instance settings stay untouched."""
        self.people.write(user_id, chosen)

    def appearance_for(self, user_id: str | None, *, language_tag: str) -> Appearance:
        """The offered language the tag names, and the person's own theme."""
        chosen = (
            PersonPreferences() if user_id is None else self.preferences_of(user_id)
        )
        return Appearance(language=offered_language(language_tag), theme=chosen.theme)

    def languages(self) -> tuple[Language, ...]:
        """Every language a select may offer."""
        return LANGUAGES

    def served_language(self) -> Language:
        """The language that answers for the instance today."""
        return offered_language(self.instance_settings().language_tag)

    def _require_newly_chosen_spoken(self, language_tag: str, *, stored: str) -> None:
        if language_tag != stored and offered_language(language_tag) != language_tag:
            raise UnspokenLanguageError(language_tag)
