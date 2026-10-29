"""The instance's defaults, a person's overrides, and how a page resolves them.

The order is the operator's ruling of 06.09.2026 on issue #8, line 22: the
person's Account preference first, the instance default behind it.
"""

from dataclasses import dataclass

from presentator.contracts.preferences import (
    Appearance,
    InstanceSettings,
    PersonPreferences,
)
from presentator.contracts.text import Catalogs, Language
from presentator.ports.preferences import (
    InstanceSettingsStore,
    PersonPreferencesStore,
)


class UnspokenLanguageError(Exception):
    """A language was chosen that no installed catalog speaks."""

    def __init__(self, language_tag: str) -> None:
        """Remember the refused tag."""
        super().__init__(language_tag)
        self.language_tag = language_tag


@dataclass(frozen=True, slots=True, kw_only=True)
class Preferences:
    """Every use case the lobby has around language and theme."""

    instance: InstanceSettingsStore
    people: PersonPreferencesStore
    catalogs: Catalogs

    def instance_settings(self) -> InstanceSettings:
        """What an admin set, or what a fresh instance starts with."""
        return self.instance.read() or InstanceSettings()

    def save_instance_settings(self, settings: InstanceSettings) -> None:
        """Keep the defaults every person without an override reads.

        Raises `UnspokenLanguageError` when no catalog speaks the language.
        """
        self._require_spoken(settings.language_tag)
        self.instance.write(settings)

    def preferences_of(self, user_id: str) -> PersonPreferences:
        """What that person chose, with an unset field meaning the instance."""
        return self.people.read(user_id) or PersonPreferences()

    def save_preferences_of(self, user_id: str, chosen: PersonPreferences) -> None:
        """Keep that person's overrides; the instance default stays untouched.

        Raises `UnspokenLanguageError` for a language newly chosen that no catalog
        speaks; the one already stored stays, so a theme change survives a
        removed catalog.
        """
        if chosen.language_tag != self.preferences_of(user_id).language_tag:
            self._require_spoken(chosen.language_tag)
        self.people.write(user_id, chosen)

    def appearance_for(self, user_id: str | None) -> Appearance:
        """The words and the look, the person's choice first, the instance's next."""
        settings = self.instance_settings()
        chosen = (
            PersonPreferences() if user_id is None else self.preferences_of(user_id)
        )
        return Appearance(
            text=self.catalogs.text(chosen.language_tag or settings.language_tag),
            theme=chosen.theme or settings.theme,
        )

    def languages(self) -> tuple[Language, ...]:
        """Every language a select may offer."""
        return self.catalogs.languages()

    def _require_spoken(self, language_tag: str | None) -> None:
        if language_tag is not None and not self.speaks(language_tag):
            raise UnspokenLanguageError(language_tag)

    def speaks(self, language_tag: str) -> bool:
        """Say whether this instance has a catalog for a submitted language."""
        return self.catalogs.speaks(language_tag)
