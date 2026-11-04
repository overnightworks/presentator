"""The person menu's theme write; the instance defaults are `/api/settings`."""

from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Form, Request, Response

from presentator.api.admission import gated_person
from presentator.api.pages import Pages
from presentator.application.preferences import Preferences
from presentator.contracts.preferences import PersonPreferences, ThemeChoice

SETTINGS: Final = "/settings"
THEME: Final = "/theme"


@dataclass(frozen=True, slots=True, kw_only=True)
class _Surfaces:
    """The menu write that carries the theme."""

    preferences: Preferences

    def choose_theme(
        self,
        request: Request,
        theme: Annotated[ThemeChoice, Form()],
    ) -> Response:
        """Write the theme row of the person menu as that person's override."""
        self.preferences.save_preferences_of(
            gated_person(request).id, PersonPreferences(theme=theme)
        )
        return _painted_again()


def preference_routes(*, pages: Pages) -> APIRouter:
    """The theme write address, for the lobby factory to include."""
    surfaces = _Surfaces(preferences=pages.preferences)
    router = APIRouter()
    router.add_api_route(THEME, surfaces.choose_theme, methods=["POST"])
    return router


def _painted_again() -> Response:
    """A person's own theme changes the whole page, not a part of it."""
    return Response(status_code=HTTPStatus.NO_CONTENT, headers={"HX-Refresh": "true"})
