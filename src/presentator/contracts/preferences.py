"""What an instance chooses about words and a person about colours."""

from dataclasses import dataclass
from enum import StrEnum

from presentator.contracts.text import DEFAULT_LANGUAGE_TAG, LobbyText


class ThemeChoice(StrEnum):
    """The three looks the picture's theme rows offer."""

    FOLLOW_SYSTEM = "follow-system"
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True, slots=True, kw_only=True)
class InstanceSettings:
    """What an admin sets for everyone, and what a fresh instance starts with."""

    name: str = ""
    language_tag: str = DEFAULT_LANGUAGE_TAG


@dataclass(frozen=True, slots=True, kw_only=True)
class PersonPreferences:
    """One person's own choice; with none, the theme follows the device."""

    theme: ThemeChoice = ThemeChoice.FOLLOW_SYSTEM


@dataclass(frozen=True, slots=True, kw_only=True)
class Appearance:
    """The words and the look one page is rendered in."""

    text: LobbyText
    theme: ThemeChoice
