"""The Sources addresses answer the lobby app's shell and keep no form route."""

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from presentator.application.decks import RESERVED_SOURCE_NAME
from presentator.contracts.decks import AccessKind
from presentator.contracts.models import Role
from tests.presentator.api.support import source_api_support
from tests.presentator.api.support.source_api_support import (
    HTTPS,
    SENT,
    a_created_source,
    renew,
)
from tests.presentator.application.support.fakes import LOCAL_MOUNT_EXAMPLE
from tests.support.lobby import (
    A_LOBBY_ASSET,
    NEIGHBOUR,
    NO_DECKS,
    USERNAME,
    GivenDecks,
    Lobby,
    a_configured_source,
    a_lobby,
    a_signed_in_lobby,
)

SOURCES_PAGE = "/settings/sources"
_ADDRESS = "git@heimserver:decks.git"
_A_BOX_FOLDER: dict[str, object] = {
    "url": f"{LOCAL_MOUNT_EXAMPLE}/talks.git",
    "access": AccessKind.FILE,
    "secret": None,
}


def an_instance_of_two_people(*, given: GivenDecks = NO_DECKS) -> Lobby:
    lobby = a_lobby(given=given)
    lobby.sign_in()
    return lobby


def signed_in_as(lobby: Lobby, username: str) -> None:
    lobby.client.post("/logout")
    lobby.sign_in(username=username)


@pytest.fixture
def instance() -> Lobby:
    return an_instance_of_two_people(
        given=GivenDecks(source=a_configured_source(_ADDRESS))
    )


@pytest.mark.parametrize(
    "form_address",
    ["/{name}/remove", "/{name}/remove/confirmed", "/{name}/fetch"],
)
def test_the_form_addresses_of_the_old_removal_and_fetch_are_gone_and_change_nothing(
    instance: Lobby, form_address: str
) -> None:
    name = "decks"
    counts = source_api_support.preview_removal(instance.client, name).json()

    posted = instance.client.post(
        SOURCES_PAGE + form_address.format(name=name),
        data={"deck_count": counts["deck_count"], "run_count": counts["run_count"]},
        headers=source_api_support.SAME_ORIGIN,
    )

    assert posted.status_code == HTTPStatus.NOT_FOUND
    assert (
        instance.client.get(
            source_api_support.PAGE.format(name=name),
            headers=source_api_support.ASKING_FOR_JSON,
        ).status_code
        == HTTPStatus.OK
    )


@pytest.mark.parametrize(
    ("changes", "name", "access", "state"),
    [
        (None, "talks", "https-token", "reachable"),
        (_A_BOX_FOLDER, "talks", "local-folder", "reachable"),
    ],
)
def test_a_created_source_is_listed_with_its_address_state_and_access(
    changes: dict[str, object] | None, name: str, access: str, state: str
) -> None:
    lobby = a_signed_in_lobby()
    a_created_source(lobby, changes)

    (listed,) = _listed(lobby)

    assert listed["name"] == name
    assert listed["url"] == (changes or {"url": HTTPS})["url"]
    assert listed["access"] == access
    assert listed["state"] == state


def test_an_existing_source_without_a_stored_secret_stays_when_another_is_added() -> (
    None
):
    lobby = a_signed_in_lobby(GivenDecks(source=a_configured_source(_ADDRESS)))

    a_created_source(lobby)

    assert {source["name"] for source in _listed(lobby)} == {"decks", "talks"}


def _listed(client: TestClient) -> list[dict[str, object]]:
    answer = client.get(
        source_api_support.LIST, headers=source_api_support.ASKING_FOR_JSON
    )
    assert answer.status_code == HTTPStatus.OK
    return answer.json()["sources"]


@pytest.mark.parametrize("username", [USERNAME, NEIGHBOUR])
@pytest.mark.parametrize("address", [SOURCES_PAGE, RESERVED_SOURCE_NAME, "talks"])
def test_the_list_add_and_a_source_address_answer_the_shell_to_any_signed_in_person(
    username: str, address: str
) -> None:
    lobby = an_instance_of_two_people()
    a_created_source(lobby.client)
    signed_in_as(lobby, username)

    answer = lobby.client.get(_page_at(address))

    assert answer.status_code == HTTPStatus.OK
    assert A_LOBBY_ASSET in answer.text
    assert answer.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("address", [SOURCES_PAGE, RESERVED_SOURCE_NAME, "talks"])
def test_a_visitor_who_is_not_signed_in_is_sent_to_sign_in_from_a_sources_address(
    address: str,
) -> None:
    page = _page_at(address)

    answer = a_lobby().client.get(page)

    assert answer.status_code == HTTPStatus.FOUND
    assert answer.headers["location"] == (
        f"/login/oidc?next={page.replace('/', '%2F')}"
    )


def _page_at(address: str) -> str:
    return address if address == SOURCES_PAGE else f"{SOURCES_PAGE}/{address}"


def two_admin_sessions() -> tuple[TestClient, TestClient]:
    """Two browser sessions over one app, each with its own cookie jar."""
    first = a_lobby()
    first.sign_in()
    second = first.another_browser()
    second.sign_in(username=NEIGHBOUR, role=Role.ADMIN)
    return first.client, second.client


def test_the_shell_at_a_sources_address_carries_neither_a_minted_secret_nor_a_sent_token() -> (  # noqa: E501 - the name states the property
    None
):
    creator, other = two_admin_sessions()
    created = a_created_source(creator)
    renewed = creator.post("/api/sources/talks/webhook-secret").json()["webhook_secret"]
    renewed_token = "the-token-this-scenario-renews"  # noqa: S105 - scenario data
    assert (
        renew(creator, "talks", {"secret": renewed_token}).status_code
        == HTTPStatus.NO_CONTENT
    )

    for session in (creator, other):
        shell = session.get(_page_at("talks"))
        assert shell.status_code == HTTPStatus.OK
        for secret in (created, renewed, SENT, renewed_token):
            assert secret not in shell.text
