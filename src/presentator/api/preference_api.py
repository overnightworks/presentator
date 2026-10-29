"""`/api/account` and `/api/settings`: the choices a page reads and writes as data.

Account is every signed-in person's own language and theme override; Settings
is the admin's instance defaults. Both answer with the languages an instance
has a catalog for, so a select needs no third address.
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


def _unspoken_beside_validation() -> dict[str, object]:
    """The PUT's 422 spec: FastAPI's own validation answer plus our problem.

    FastAPI generates the validation entry (and its components) only while the
    route declares no 422 itself, so the problem's media type joins it afterwards.
    """
    status = HTTPStatus.UNPROCESSABLE_ENTITY
    return {"responses": {str(status.value): problem_response(status)[status.value]}}


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
    unspoken = _unspoken_beside_validation()
    admin_only = problem_response(HTTPStatus.FORBIDDEN)
    router.add_api_route(ACCOUNT_PATH, read_account, methods=["GET"])
    router.add_api_route(
        ACCOUNT_PATH, write_account, methods=["PUT"], openapi_extra=unspoken
    )
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
    """The router that reads and writes overrides and instance defaults."""

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
        person_id = gated_person(request).id
        with _unspoken_as_problem():
            preferences.save_preferences_of(
                person_id,
                PersonPreferences(language_tag=body.language_tag, theme=body.theme),
            )
        return account_of(person_id)

    def read_settings() -> SettingsView:
        return settings()

    def write_settings(body: SettingsWrite) -> SettingsView:
        with _unspoken_as_problem():
            preferences.save_instance_settings(
                InstanceSettings(
                    name=body.name, language_tag=body.language_tag, theme=body.theme
                )
            )
        return settings()

    return _mounted(
        read_account=read_account,
        write_account=write_account,
        read_settings=read_settings,
        write_settings=write_settings,
    )
