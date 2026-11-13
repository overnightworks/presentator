"""What an instance names for everyone and a person chooses about colours."""

from dataclasses import dataclass
from enum import StrEnum

from presentator.contracts.words import Language


class ThemeChoice(StrEnum):
    """The three looks a person can choose: follow the system, light or dark."""

    FOLLOW_SYSTEM = "follow-system"
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True, slots=True, kw_only=True)
class InstanceSettings:
    """What an admin sets for everyone, and what a fresh instance starts with."""

    name: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class PersonPreferences:
    """One person's own choice; with none, the theme follows the device."""

    theme: ThemeChoice = ThemeChoice.FOLLOW_SYSTEM


@dataclass(frozen=True, slots=True, kw_only=True)
class Appearance:
    """The language and the look one page is rendered in."""

    language: Language
    theme: ThemeChoice
