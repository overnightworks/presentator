"""`GET /api/session`: who reads, in which language and theme, and where sign-in is.

The session gate lets this one path through without a session, so a signed-out
caller gets the browser's language and the default theme; the person comes from what
the gate admitted. The language is the account's where a catalog speaks it, else the
browser's, else the catalog source's.
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
from presentator.application.preferences import Preferences, offered_language
from presentator.contracts.models import Role
from presentator.contracts.preferences import Appearance, ThemeChoice
from presentator.contracts.words import LANGUAGES, SOURCE_LANGUAGE, Language

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
    language: Language
    sign_in: SessionAddresses


def language_of(request: Request, sessions: SessionRecordStore) -> Language:
    """The offered language the person behind this request reads in.

    The account's language where a catalog speaks it, else the browser's, else the
    catalog source's.
    """
    language_tag = resolve_language(
        AppLanguages(offered=LANGUAGES, default=SOURCE_LANGUAGE),
        account_tag=account_language_of_session(
            request, sessions=sessions, config=web_auth_config(request)
        ),
        accept_language=request.headers.get("accept-language"),
    )
    return offered_language(language_tag)


def appearance_of(
    request: Request, preferences: Preferences, sessions: SessionRecordStore
) -> Appearance:
    """The language and the look the person behind this request reads in."""
    person = signed_in_person(request)
    return preferences.appearance_for(
        None if person is None else person.id,
        language=language_of(request, sessions),
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
        """Show who is signed in, their language tag and theme, and where to sign in.

        The answer carries the language as a tag and no sentence.
        """
        person = signed_in_person(request)
        appearance = appearance_of(request, preferences, sessions)
        return SessionView(
            person=None
            if person is None
            else SessionPerson(username=person.username, role=person.role),
            theme=appearance.theme,
            language=appearance.language,
            sign_in=addresses,
        )

    return router
