"""`GET /api/session`: who reads, in which words and theme, and where sign-in is.

The session gate lets this one path through without a session, so a signed-out
caller gets the instance's defaults; the person comes from what the gate admitted.
"""

from typing import Final

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from presentator.api.admission import signed_in_person
from presentator.application.preferences import Preferences
from presentator.contracts.models import Role
from presentator.contracts.preferences import ThemeChoice
from presentator.contracts.text import LobbyText

SESSION_PATH: Final = "/api/session"


class SessionAddresses(BaseModel):
    """Where a page sends a person to sign in or out."""

    model_config = ConfigDict(frozen=True)

    start: str
    logout: str
    talk_start: str


class SessionPerson(BaseModel):
    """The signed-in person as the page shows them."""

    model_config = ConfigDict(frozen=True)

    username: str
    role: Role


class SessionView(BaseModel):
    """Everything a page needs to know about the reader before it renders."""

    person: SessionPerson | None
    theme: ThemeChoice
    words: LobbyText
    sign_in: SessionAddresses


def session_routes(
    *, preferences: Preferences, addresses: SessionAddresses
) -> APIRouter:
    """The router that answers the session view."""
    router = APIRouter()

    @router.get(SESSION_PATH)
    def session(request: Request) -> SessionView:
        person = signed_in_person(request)
        appearance = preferences.appearance_for(None if person is None else person.id)
        return SessionView(
            person=None
            if person is None
            else SessionPerson(username=person.username, role=person.role),
            theme=appearance.theme,
            words=appearance.text,
            sign_in=addresses,
        )

    return router
