"""A lobby over a chosen `Decks`, for the Sources writes and their denials."""

from dataclasses import dataclass, replace
from http import HTTPStatus
from typing import cast

from fastapi.testclient import TestClient
from httpx2 import Response

from presentator.application.decks import Decks
from presentator.contracts.decks import Source
from presentator.ports.decks import DeckStore, DeployKeyDrafts, SourceRuns, SourceStore
from tests.presentator.application.support.fakes import (
    ConnectionCheckerThatMustNotRun,
    DeckFoldersThatMustNotRun,
    FakeSourceStore,
    FrozenClock,
)
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_BROWSERS_HEADERS,
    NEIGHBOUR,
    NOW,
    GivenDecks,
    Lobby,
    SessionsOnly,
    SigningIn,
    a_browser_at,
    a_crowd,
    a_decks,
    a_lobby_app,
    an_installed_auth,
)
from tests.support.sign_in_support import Scene

ACCESS = "/api/sources/{name}/access"
PAGE = "/api/sources/{name}"
LIST = "/api/sources"
PROBLEM = "application/problem+json"
ASKING_FOR_JSON = {"accept": "application/json"}
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
HAL = "hal-client"
STORED_BEFORE = "the-token-this-scenario-replaces"
SENT_NOW = "the-token-this-scenario-sends"


class PortThatMustNotRun:
    """Stands in for a port; any use of it fails the test that arranged it."""

    def __getattr__(self, name: str) -> object:
        """Fail with the name of the member somebody reached for."""
        raise AssertionError(name)


def decks_over(store: FakeSourceStore) -> Decks:
    """The deck use cases over this source store and fakes elsewhere."""
    return replace(a_decks(GivenDecks(), FrozenClock(instant=NOW)), sources=store)


def decks_that_must_not_run() -> Decks:
    """Deck use cases whose every port fails the test that touches it."""
    return replace(
        decks_over(FakeSourceStore()),
        sources=cast("SourceStore", PortThatMustNotRun()),
        key_drafts=cast("DeployKeyDrafts", PortThatMustNotRun()),
        folders=DeckFoldersThatMustNotRun(),
        store=cast("DeckStore", PortThatMustNotRun()),
        source_runs=cast("SourceRuns", PortThatMustNotRun()),
        checker=ConnectionCheckerThatMustNotRun(),
    )


def a_source_store(*sources: Source, secret: str | None = None) -> FakeSourceStore:
    """The store carrying these sources, each with the given access secret."""
    return FakeSourceStore(
        sources=list(sources),
        secrets={} if secret is None else {source.id: secret for source in sources},
    )


def a_lobby_over(decks: Decks, *, headers: dict[str, str] | None = None) -> Lobby:
    """A lobby over exactly these deck use cases, nobody signed in yet."""
    people = a_crowd()
    app, _ = a_lobby_app(
        people=people,
        signing_in=SigningIn(
            sessions=people.sessions,
            auth=an_installed_auth(
                people.clock, transaction=SessionsOnly(people.sessions)
            ),
            decks=decks,
        ),
    )
    return a_browser_at(
        app,
        people=people,
        base_url=A_BROWSERS_ADDRESS,
        headers=A_BROWSERS_HEADERS if headers is None else headers,
    )


def an_admin_over(decks: Decks, *, headers: dict[str, str] | None = None) -> Lobby:
    lobby = a_lobby_over(decks, headers=headers)
    lobby.sign_in()
    return lobby


def a_member_over(decks: Decks) -> Lobby:
    lobby = a_lobby_over(decks)
    lobby.sign_in(username=NEIGHBOUR)
    return lobby


def without_raising(lobby: Lobby) -> TestClient:
    """The signed-in browser, answering a server error instead of raising it."""
    return TestClient(
        lobby.app,
        base_url=str(lobby.client.base_url),
        follow_redirects=False,
        headers=dict(lobby.client.headers),
        cookies=lobby.client.cookies,
        raise_server_exceptions=False,
    )


def a_bearer(scene: Scene, groups: list[str]) -> dict[str, str]:
    """The headers of a person's access token minted for the hal client."""
    minted = scene.issuer.mint_access_token(
        subject="a-person", groups=groups, authorized_party=HAL, audience=None
    )
    return {"authorization": f"Bearer {minted}", **ASKING_FOR_JSON}


def renew(
    client: TestClient,
    name: str,
    body: object,
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    """Post this JSON document to the source's renewal."""
    return client.post(ACCESS.format(name=name), json=body, headers=headers)


def a_client_with_no_header_of_its_own(scene: Scene) -> TestClient:
    """Reaches the scene's lobby with nothing but what the test adds per request."""
    return TestClient(
        scene.lobby.app, base_url=A_BROWSERS_ADDRESS, follow_redirects=False
    )


def assert_no_store(answer: Response) -> None:
    assert answer.headers["cache-control"] == "no-store"


def assert_nothing_returned(answer: Response) -> None:
    """No answer, in its body or its headers, carries either scenario token."""
    shown = answer.text + str(answer.headers)
    assert SENT_NOW not in shown
    assert STORED_BEFORE not in shown


def assert_validation_answer(answer: Response, *sent: str) -> None:
    """The framework's 422 shape, never repeating what the request carried."""
    assert answer.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert_no_store(answer)
    assert all(
        set(fault) == {"loc", "msg", "type"} for fault in answer.json()["detail"]
    )
    assert_nothing_returned(answer)
    for each in sent:
        assert each not in answer.text


def assert_problem(answer: Response, status: HTTPStatus) -> None:
    assert answer.status_code == status
    assert answer.headers["content-type"] == PROBLEM
    assert_no_store(answer)
    assert_nothing_returned(answer)


@dataclass(frozen=True, slots=True)
class Instance:
    """A client over a store the test reads back."""

    client: TestClient
    store: FakeSourceStore
    headers: dict[str, str]

    def renew(self, name: str, body: object) -> Response:
        return renew(self.client, name, body, headers=self.headers)
