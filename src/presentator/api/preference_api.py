"""`/api/account` and `/api/settings`: the choices a page reads and writes as data.

Account is the person's own theme; Settings is the admin's instance name.
"""

from collections.abc import Callable
from http import HTTPStatus
from typing import Final

from agents.tools import ToolEffect, agent_tool
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from presentator.api.admission import (
    administration_is_no_tool,
    gated_admin,
    gated_person,
)
from presentator.api.problems import problem_response
from presentator.application.preferences import Preferences
from presentator.contracts.preferences import (
    InstanceSettings,
    PersonPreferences,
    ThemeChoice,
)

ACCOUNT_PATH: Final = "/api/account"
SETTINGS_PATH: Final = "/api/settings"


class AccountView(BaseModel):
    """One person's own theme, read and written.

    No default: one schema serves the body and the answer.
    """

    model_config = ConfigDict(frozen=True)

    theme: ThemeChoice


class SettingsView(BaseModel):
    """The instance name an admin sets for everyone, read and written.

    No default: one schema serves the body and the answer.
    """

    model_config = ConfigDict(frozen=True)

    name: str


def _mounted(
    *,
    read_account: Callable[..., AccountView],
    write_account: Callable[..., AccountView],
    read_settings: Callable[..., SettingsView],
    write_settings: Callable[..., SettingsView],
) -> APIRouter:
    """The four addresses, with the admin gate and the documented refusals."""
    router = APIRouter()
    admin_only = problem_response(HTTPStatus.FORBIDDEN)
    router.add_api_route(
        ACCOUNT_PATH,
        read_account,
        methods=["GET"],
        openapi_extra=agent_tool(name="get_account", effect=ToolEffect.READ_ONLY),
    )
    router.add_api_route(
        ACCOUNT_PATH,
        write_account,
        methods=["PUT"],
        openapi_extra=agent_tool(
            name="set_account_theme", effect=ToolEffect.SIDE_EFFECTING
        ),
    )
    admin = [Depends(gated_admin)]
    router.add_api_route(
        SETTINGS_PATH,
        read_settings,
        methods=["GET"],
        dependencies=admin,
        responses=admin_only,
        openapi_extra=administration_is_no_tool(),
    )
    router.add_api_route(
        SETTINGS_PATH,
        write_settings,
        methods=["PUT"],
        dependencies=admin,
        responses=admin_only,
        openapi_extra=administration_is_no_tool(),
    )
    return router


def preference_api_routes(*, preferences: Preferences) -> APIRouter:
    """The router that reads and writes a person's theme and the instance settings."""

    def account_of(person_id: str) -> AccountView:
        chosen = preferences.preferences_of(person_id)
        return AccountView(theme=chosen.theme)

    def settings() -> SettingsView:
        stored = preferences.instance_settings()
        return SettingsView(name=stored.name)

    def read_account(request: Request) -> AccountView:
        """Read the signed-in person's own theme."""
        return account_of(gated_person(request).id)

    def write_account(request: Request, body: AccountView) -> AccountView:
        """Set the signed-in person's own theme and answer the saved choice."""
        person_id = gated_person(request).id
        preferences.save_preferences_of(person_id, PersonPreferences(theme=body.theme))
        return account_of(person_id)

    def read_settings() -> SettingsView:
        return settings()

    def write_settings(body: SettingsView) -> SettingsView:
        preferences.save_instance_settings(InstanceSettings(name=body.name))
        return settings()

    return _mounted(
        read_account=read_account,
        write_account=write_account,
        read_settings=read_settings,
        write_settings=write_settings,
    )
