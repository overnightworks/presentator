"""`/api/account` and `/api/settings`: the choices a page reads and writes as data.

Account is the person's own theme; Settings is the admin's instance name and
language and carries the language list, so a select needs no third address.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from http import HTTPStatus
from typing import Final

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from presentator.api.admission import gated_admin, gated_person
from presentator.api.problems import (
    ProblemError,
    problem_beside_validation,
    problem_response,
)
from presentator.application.preferences import Preferences, UnspokenLanguageError
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


class AccountView(BaseModel):
    """One person's own theme, read and written.

    No default: one schema serves the body and the answer.
    """

    model_config = ConfigDict(frozen=True)

    theme: ThemeChoice


class SettingsWrite(BaseModel):
    """The instance name and language an admin sets for everyone."""

    model_config = ConfigDict(frozen=True)

    name: str
    language_tag: str


class SettingsView(SettingsWrite):
    """The instance settings and the languages an admin may choose from."""

    languages: list[LanguageView]


@contextmanager
def _unspoken_as_problem() -> Generator[None]:
    """Answer the application's refusal of an unspoken language as the 422 problem."""
    try:
        yield
    except UnspokenLanguageError:
        raise ProblemError(HTTPStatus.UNPROCESSABLE_ENTITY) from None


def _mounted(
    *,
    read_account: Callable[..., AccountView],
    write_account: Callable[..., AccountView],
    read_settings: Callable[..., SettingsView],
    write_settings: Callable[..., SettingsView],
) -> APIRouter:
    """The four addresses, with the admin gate and the documented refusals."""
    router = APIRouter()
    unspoken = problem_beside_validation()
    admin_only = problem_response(HTTPStatus.FORBIDDEN)
    router.add_api_route(ACCOUNT_PATH, read_account, methods=["GET"])
    router.add_api_route(ACCOUNT_PATH, write_account, methods=["PUT"])
    admin = [Depends(gated_admin)]
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
        responses=admin_only,
        openapi_extra=unspoken,
    )
    return router


def preference_api_routes(*, preferences: Preferences) -> APIRouter:
    """The router that reads and writes a person's theme and the instance settings."""

    def languages() -> list[LanguageView]:
        return [
            LanguageView(tag=language.tag, name=language.name)
            for language in preferences.languages()
        ]

    def account_of(person_id: str) -> AccountView:
        chosen = preferences.preferences_of(person_id)
        return AccountView(theme=chosen.theme)

    def settings() -> SettingsView:
        stored = preferences.instance_settings()
        return SettingsView(
            name=stored.name,
            language_tag=stored.language_tag,
            languages=languages(),
        )

    def read_account(request: Request) -> AccountView:
        return account_of(gated_person(request).id)

    def write_account(request: Request, body: AccountView) -> AccountView:
        person_id = gated_person(request).id
        preferences.save_preferences_of(person_id, PersonPreferences(theme=body.theme))
        return account_of(person_id)

    def read_settings() -> SettingsView:
        return settings()

    def write_settings(body: SettingsWrite) -> SettingsView:
        with _unspoken_as_problem():
            preferences.save_instance_settings(
                InstanceSettings(name=body.name, language_tag=body.language_tag)
            )
        return settings()

    return _mounted(
        read_account=read_account,
        write_account=write_account,
        read_settings=read_settings,
        write_settings=write_settings,
    )
