"""Settings for the whole instance and Account for one person (ADR 0012).

Settings is admin only, and a person's own choices live on Account; the person
menu's theme rows write the same Account preference (issue #8, lines 21 to 23).
"""

from dataclasses import dataclass, replace
from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Form, Request, Response
from pydantic import BeforeValidator

from presentator.api.admission import gated_person
from presentator.api.pages import Pages, options, theme_choices
from presentator.application.preferences import Preferences
from presentator.contracts.preferences import PersonPreferences, ThemeChoice

SETTINGS: Final = "/settings"
ACCOUNT: Final = "/account"
THEME: Final = "/theme"

_NO_OVERRIDE: Final = ""


def _left_to_the_instance(submitted: str | None) -> str | None:
    """An empty choice on Account means: whatever the instance says."""
    return submitted or None


_LanguageOverride = Annotated[
    str | None,
    BeforeValidator(_left_to_the_instance),
    Form(),
]
_ThemeOverride = Annotated[
    ThemeChoice | None,
    BeforeValidator(_left_to_the_instance),
    Form(),
]


@dataclass(frozen=True, slots=True, kw_only=True)
class _Surfaces:
    """The two pages and the menu write that carry language and theme."""

    pages: Pages
    preferences: Preferences

    def account_page(self, request: Request) -> Response:
        """Show one person what is theirs."""
        person = gated_person(request)
        text = self.pages.appearance(request).text
        chosen = self.preferences.preferences_of(person.id)
        instance_default = (_NO_OVERRIDE, text.account_instance_default)
        return self.pages.page(
            request,
            "account.html",
            role=text.role_admin if person.is_admin else text.role_user,
            languages=options(
                (instance_default, *self._installed_languages()),
                chosen=chosen.language_tag or _NO_OVERRIDE,
            ),
            themes=options(
                (instance_default, *theme_choices(text)),
                chosen=chosen.theme or _NO_OVERRIDE,
            ),
        )

    def save_account(
        self,
        request: Request,
        language: _LanguageOverride = None,
        theme: _ThemeOverride = None,
    ) -> Response:
        """Keep what this person alone reads, leaving the instance untouched."""
        if language is not None and not self.preferences.speaks(language):
            return _unspoken_language()
        self.preferences.save_preferences_of(
            gated_person(request).id,
            PersonPreferences(language_tag=language, theme=theme),
        )
        return _painted_again()

    def choose_theme(
        self,
        request: Request,
        theme: Annotated[ThemeChoice, Form()],
    ) -> Response:
        """Write the theme row of the person menu into the Account preference."""
        person = gated_person(request)
        self.preferences.save_preferences_of(
            person.id,
            replace(self.preferences.preferences_of(person.id), theme=theme),
        )
        return _painted_again()

    def _installed_languages(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (language.tag, language.name) for language in self.preferences.languages()
        )


def preference_routes(*, pages: Pages) -> APIRouter:
    """The Settings and Account addresses, for the lobby factory to include."""
    surfaces = _Surfaces(pages=pages, preferences=pages.preferences)
    router = APIRouter()
    router.add_api_route(ACCOUNT, surfaces.account_page, methods=["GET"])
    router.add_api_route(ACCOUNT, surfaces.save_account, methods=["POST"])
    router.add_api_route(THEME, surfaces.choose_theme, methods=["POST"])
    return router


def _unspoken_language() -> Response:
    """A language nobody installed a catalog for is not a choice this instance has."""
    return Response(status_code=HTTPStatus.UNPROCESSABLE_ENTITY)


def _painted_again() -> Response:
    """A person's own language or theme changes the whole page, not a part of it."""
    return Response(status_code=HTTPStatus.NO_CONTENT, headers={"HX-Refresh": "true"})
