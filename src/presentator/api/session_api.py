"""`GET /api/session`: who reads, in which words and theme, and where sign-in is.

The session gate lets this one path through without a session, so a signed-out
caller gets the instance's defaults; the person comes from what the gate admitted.
The language is the account's where a catalog speaks it, else the instance's.
"""

from typing import Final

from agents.tools import ToolEffect, agent_tool
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from webauth.config import web_auth_config
from webauth.dependencies import account_language_of_session
from webauth.languages import AppLanguages, resolve_language
from webauth.ports import SessionRecordStore

from presentator.api.admission import signed_in_person
from presentator.application.preferences import Preferences
from presentator.contracts.models import Role
from presentator.contracts.preferences import Appearance, ThemeChoice
from presentator.contracts.text import LobbyText

SESSION_PATH: Final = "/api/session"


class SessionAddresses(BaseModel):
    """Where a page sends a person to sign in or out, or to the central account."""

    model_config = ConfigDict(frozen=True)

    start: str
    logout: str
    talk_start: str
    account_console_url: str


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


def appearance_of(
    request: Request, preferences: Preferences, sessions: SessionRecordStore
) -> Appearance:
    """The words and the look the person behind this request reads in.

    The browser's own language is not read: the account's names the language,
    else the instance's.
    """
    person = signed_in_person(request)
    language_tag = resolve_language(
        AppLanguages(
            offered=preferences.offered_tags(), default=preferences.served_tag()
        ),
        account_tag=account_language_of_session(
            request, sessions=sessions, config=web_auth_config(request)
        ),
        accept_language=None,
    )
    return preferences.appearance_for(
        None if person is None else person.id, language_tag=language_tag
    )


def session_routes(
    *,
    preferences: Preferences,
    sessions: SessionRecordStore,
    addresses: SessionAddresses,
) -> APIRouter:
    """The router that answers the session view."""
    router = APIRouter()

    @router.get(
        SESSION_PATH,
        openapi_extra=agent_tool(name="get_session", effect=ToolEffect.READ_ONLY),
    )
    def session(request: Request) -> SessionView:
        """Show who is signed in, the language and theme they read in, and the words."""
        person = signed_in_person(request)
        appearance = appearance_of(request, preferences, sessions)
        return SessionView(
            person=None
            if person is None
            else SessionPerson(username=person.username, role=person.role),
            theme=appearance.theme,
            words=appearance.text,
            sign_in=addresses,
        )

    return router
