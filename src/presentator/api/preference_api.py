"""`/api/account` and `/api/settings`: the choices a page reads and writes as data.

Account is every signed-in person's own language and theme override; Settings
is the admin's instance defaults. Both answer with the languages an instance
has a catalog for, so a select needs no third address.
"""

from http import HTTPStatus
from typing import Final

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from presentator.api.admission import gated_person
from presentator.api.problems import ProblemError, problem_response
from presentator.application.preferences import Preferences
from presentator.contracts.preferences import (
    InstanceSettings,
    PersonPreferences,
    ThemeChoice,
)

ACCOUNT_PATH: Final = "/api/account"
SETTINGS_PATH: Final = "/api/settings"


class LanguageView(BaseModel):
    """A language an instance can speak, named the way its own catalog names it."""

    model_config = ConfigDict(frozen=True)

    tag: str
    name: str


class AccountWrite(BaseModel):
    """Both overrides of one person; `None` leaves the choice to the instance."""

    model_config = ConfigDict(frozen=True)

    language_tag: str | None
    theme: ThemeChoice | None


class AccountView(AccountWrite):
    """One person's overrides and the languages they may choose from."""

    languages: list[LanguageView]


class SettingsWrite(BaseModel):
    """The instance defaults an admin sets for everyone without an override."""

    model_config = ConfigDict(frozen=True)

    name: str
    language_tag: str
    theme: ThemeChoice


class SettingsView(SettingsWrite):
    """The instance defaults and the languages an admin may choose from."""

    languages: list[LanguageView]


def _only_an_admin(request: Request) -> None:
    """Refuse a person without the role before any body, read or write."""
    if not gated_person(request).is_admin:
        raise ProblemError(HTTPStatus.FORBIDDEN)


def preference_api_routes(*, preferences: Preferences) -> APIRouter:
    """The router that reads and writes overrides and instance defaults."""
    router = APIRouter()

    def languages() -> list[LanguageView]:
        return [
            LanguageView(tag=language.tag, name=language.name)
            for language in preferences.languages()
        ]

    def account_of(person_id: str) -> AccountView:
        chosen = preferences.preferences_of(person_id)
        return AccountView(
            language_tag=chosen.language_tag,
            theme=chosen.theme,
            languages=languages(),
        )

    def settings() -> SettingsView:
        stored = preferences.instance_settings()
        return SettingsView(
            name=stored.name,
            language_tag=stored.language_tag,
            theme=stored.theme,
            languages=languages(),
        )

    def read_account(request: Request) -> AccountView:
        return account_of(gated_person(request).id)

    def write_account(request: Request, body: AccountWrite) -> AccountView:
        if body.language_tag is not None and not preferences.speaks(body.language_tag):
            raise ProblemError(HTTPStatus.UNPROCESSABLE_ENTITY)
        person_id = gated_person(request).id
        preferences.save_preferences_of(
            person_id,
            PersonPreferences(language_tag=body.language_tag, theme=body.theme),
        )
        return account_of(person_id)

    def read_settings() -> SettingsView:
        return settings()

    def write_settings(body: SettingsWrite) -> SettingsView:
        if not preferences.speaks(body.language_tag):
            raise ProblemError(HTTPStatus.UNPROCESSABLE_ENTITY)
        preferences.save_instance_settings(
            InstanceSettings(
                name=body.name, language_tag=body.language_tag, theme=body.theme
            )
        )
        return settings()

    unspoken = problem_response(HTTPStatus.UNPROCESSABLE_ENTITY)
    admin_only = problem_response(HTTPStatus.FORBIDDEN)
    router.add_api_route(ACCOUNT_PATH, read_account, methods=["GET"])
    router.add_api_route(
        ACCOUNT_PATH, write_account, methods=["PUT"], responses=unspoken
    )
    admin = [Depends(_only_an_admin)]
    router.add_api_route(
        SETTINGS_PATH,
        read_settings,
        methods=["GET"],
        dependencies=admin,
        responses=admin_only,
    )
    router.add_api_route(
        SETTINGS_PATH,
        write_settings,
        methods=["PUT"],
        dependencies=admin,
        responses={**admin_only, **unspoken},
    )
    return router
