"""What an instance and a person choose about words and colours (ADR 0012)."""

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
    theme: ThemeChoice = ThemeChoice.FOLLOW_SYSTEM


@dataclass(frozen=True, slots=True, kw_only=True)
class PersonPreferences:
    """One person's override; an unset one leaves the choice to the instance."""

    theme: ThemeChoice | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Appearance:
    """The words and the look one page is rendered in."""

    text: LobbyText
    theme: ThemeChoice
