"""A lobby over a chosen `Decks`, for the Sources writes and their denials."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from http import HTTPStatus
from pathlib import Path
from typing import Final, cast

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from presentator.api.hooks import hook_address
from presentator.application.decks import RESERVED_SOURCE_NAME, Decks
from presentator.contracts.decks import ConnectionCheckResult, Source
from presentator.ports.decks import (
    DeckStore,
    DeployKeyDrafts,
    SourceRuns,
    SourceStore,
    WebhookSecrets,
)
from tests.presentator.application.support.fakes import (
    ConnectionCheckerThatMustNotRun,
    DeckFoldersThatMustNotRun,
    FakeConnectionChecker,
    FakeDeployKeyDrafts,
    FakeSourceStore,
    FakeWebhookSecrets,
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
from tests.support.lobby import (
    ADMIN as ADMIN_PERSON,
)
from tests.support.sign_in_support import ADMIN as ADMIN_GROUPS
from tests.support.sign_in_support import Scene, a_scene

ACCESS = "/api/sources/{name}/access"
WEBHOOK_RENEWAL = "/api/sources/{name}/webhook-secret"
PAGE = "/api/sources/{name}"
LIST = "/api/sources"
PROBLEM = "application/problem+json"
ASKING_FOR_JSON = {"accept": "application/json"}
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
HAL = "hal-client"
NEW = f"/api/sources/{RESERVED_SOURCE_NAME}"
KEY_DRAFT = f"{NEW}/key-draft"
CHECK = f"{NEW}/check"
A_SOURCE_NAME = "talks"
SENT = "the-token-this-scenario-sends"
HTTPS = "https://git.example.invalid/talks.git"


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
        webhook_secrets=cast("WebhookSecrets", PortThatMustNotRun()),
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


def an_admin_cookie_instance(decks: Decks, store: FakeSourceStore) -> Instance:
    """The signed-in admin browser, reaching the lobby by its session cookie."""
    return Instance(an_admin_over(decks).client, store, {}, ADMIN_PERSON)


def an_admin_bearer_instance(
    decks: Decks, store: FakeSourceStore, tmp_path: Path
) -> Instance:
    """An admin reaching the lobby by a bearer alone, with no cookie."""
    scene = a_scene(tmp_path, token_clients=frozenset({HAL}), decks=decks)
    return Instance(
        a_client_with_no_header_of_its_own(scene),
        store,
        a_bearer(scene, ADMIN_GROUPS),
        BEARER_PERSON,
    )


def a_scene_that_must_not_run(tmp_path: Path) -> Scene:
    """A lobby with a bearer client whose every deck port fails the test."""
    return a_scene(
        tmp_path, token_clients=frozenset({HAL}), decks=decks_that_must_not_run()
    )


def whole_answers_of(instance: Instance, name: str) -> str:
    """Everything the Sources data says about the instance's sources."""
    headers = {**instance.headers, **ASKING_FOR_JSON}
    answers = (
        instance.client.get(LIST, headers=headers),
        instance.client.get(PAGE.format(name=name), headers=headers),
    )
    for answer in answers:
        assert answer.status_code == HTTPStatus.OK
        assert_no_store(answer)
    return "".join(answer.text for answer in answers)


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


BEARER_PERSON: Final = "a-person"


def a_bearer(scene: Scene, groups: list[str]) -> dict[str, str]:
    """The headers of a person's access token minted for the hal client."""
    minted = scene.issuer.mint_access_token(
        subject=BEARER_PERSON, groups=groups, authorized_party=HAL, audience=None
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


def renew_webhook(
    client: TestClient,
    name: str,
    headers: dict[str, str] | None = None,
    *,
    content: str | None = None,
    body: object = None,
) -> Response:
    """Post to the source's webhook-secret renewal; it reads no body of its own.

    Every answer, whatever it is, must not be stored by a cache.
    """
    answer = client.post(
        WEBHOOK_RENEWAL.format(name=name), headers=headers, content=content, json=body
    )
    assert_no_store(answer)
    return answer


def a_client_with_no_header_of_its_own(scene: Scene) -> TestClient:
    """Reaches the scene's lobby with nothing but what the test adds per request."""
    return TestClient(
        scene.lobby.app, base_url=A_BROWSERS_ADDRESS, follow_redirects=False
    )


def assert_no_store(answer: Response) -> None:
    assert answer.headers["cache-control"] == "no-store"


def assert_nothing_returned(answer: Response, *secrets: str) -> None:
    """No answer, in its body or its headers, carries any of these values."""
    shown = answer.text + str(answer.headers)
    for each in secrets:
        assert each not in shown


def assert_validation_answer(answer: Response, *sent: str) -> None:
    """The framework's 422 shape, never repeating what the request carried."""
    assert answer.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert_no_store(answer)
    assert all(
        set(fault) == {"loc", "msg", "type"} for fault in answer.json()["detail"]
    )
    assert_nothing_returned(answer, *sent)


def assert_problem(answer: Response, status: HTTPStatus, *secrets: str) -> None:
    assert answer.status_code == status
    assert answer.headers["content-type"] == PROBLEM
    assert_no_store(answer)
    assert_nothing_returned(answer, *secrets)


@dataclass(frozen=True, slots=True)
class Instance:
    """A client over a store the test reads back."""

    client: TestClient
    store: FakeSourceStore
    headers: dict[str, str]
    person_id: str

    def renew(self, name: str, body: object) -> Response:
        return renew(self.client, name, body, headers=self.headers)


@dataclass
class RecordingChecker(FakeConnectionChecker):
    """A connection checker that remembers each address and secret it was asked."""

    asked: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    def check(self, *, url: str, ref: str, secret: str) -> ConnectionCheckResult:
        self.asked.append((url, secret))
        return self.answer


@dataclass(frozen=True, slots=True)
class World:
    """The deck use cases an admin's requests reach, and what they can be read from."""

    decks: Decks
    clock: FrozenClock
    checker: RecordingChecker
    store: FakeSourceStore
    drafts: FakeDeployKeyDrafts
    webhook_secrets: FakeWebhookSecrets


def a_world(checker: RecordingChecker | None = None) -> World:
    drafts = FakeDeployKeyDrafts()
    store = FakeSourceStore(key_drafts=drafts)
    clock = FrozenClock(instant=decks_over(store).clock.now())
    recording = RecordingChecker() if checker is None else checker
    webhook_secrets = FakeWebhookSecrets()
    decks = replace(
        decks_over(store),
        checker=recording,
        clock=clock,
        key_drafts=drafts,
        webhook_secrets=webhook_secrets,
    )
    return World(decks, clock, recording, store, drafts, webhook_secrets)


def _cookie(world: World, _tmp_path: Path) -> Instance:
    return an_admin_cookie_instance(world.decks, world.store)


def _bearer(world: World, tmp_path: Path) -> Instance:
    return an_admin_bearer_instance(world.decks, world.store, tmp_path)


type AdminReach = Callable[[World, Path], Instance]

AN_ADMIN_BY_COOKIE_AND_BEARER: Final = pytest.mark.parametrize(
    "reach", [_cookie, _bearer], ids=["cookie", "bearer"]
)


def checked_body(**changes: object) -> dict[str, object]:
    """A check or create request for an HTTPS source, with these fields changed."""
    return {
        "url": HTTPS,
        "access": "https-token",
        "secret": SENT,
        "key_draft_id": None,
    } | changes


def draft(client: TestClient, headers: dict[str, str] | None = None) -> Response:
    return client.post(KEY_DRAFT, headers=headers)


def check(client: TestClient, headers: dict[str, str] | None = None) -> Response:
    return client.post(CHECK, json=checked_body(), headers=headers)


def a_created_source(
    client: TestClient,
    changes: dict[str, object] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> str:
    """Create the source through the API; the webhook secret it came with."""
    body = checked_body(
        **{"name": A_SOURCE_NAME} | ({} if changes is None else changes)
    )
    body["fingerprint"] = client.post(CHECK, json=body, headers=headers).json()[
        "fingerprint"
    ]
    answer = client.post(LIST, json=body, headers=headers)
    assert answer.status_code == HTTPStatus.CREATED
    return answer.json()["webhook_secret"]


def calling_hook(client: TestClient, name: str, *, offered: str) -> Response:
    """Call the source's hook with this secret; no answer may be stored by a cache."""
    answer = client.post(
        hook_address(name), headers={"authorization": f"Bearer {offered}"}
    )
    assert_no_store(answer)
    return answer
