"""The language token the co-presenter speaks in."""

from enum import StrEnum


class SampleLanguage(StrEnum):
    """The closed browser-to-speech language token for a fixed voice sample."""

    GERMAN = "de"
    ENGLISH = "en"
