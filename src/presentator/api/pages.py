"""The words and the look each reader chose, resolved per request.

Every page resolves its own theme, a person's choice (ADR 0012), and the
instance's language, rather than a property of the instance the routes were
built with.
"""

from dataclasses import dataclass

from fastapi import Request

from presentator.api.admission import signed_in_person
from presentator.application.preferences import Preferences
from presentator.contracts.preferences import Appearance


@dataclass(frozen=True, slots=True, kw_only=True)
class Pages:
    """Resolves the words and the look each reader chose."""

    preferences: Preferences

    def appearance(self, request: Request) -> Appearance:
        """The words and the look the person behind this request reads in."""
        person = signed_in_person(request)
        return self.preferences.appearance_for(None if person is None else person.id)
