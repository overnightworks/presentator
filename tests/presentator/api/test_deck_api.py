"""`GET /api/decks/{slug}` tells a page one deck's facts, and errors as a problem."""

from datetime import datetime, timedelta
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from presentator.api.deck_api import DeckView
from presentator.contracts.decks import BuildAttempt, BuildOutcome, DeckState
from presentator.contracts.models import Role
from tests.presentator.application.support.fakes import FakeDeckStore
from tests.support.deck_page_support import SLUG, a_build, a_deck_store
from tests.support.lobby import (
    A_TALK_ADDRESS,
    ADMIN,
    NEIGHBOUR,
    NOW,
    GivenDecks,
    Lobby,
    a_configured_source,
    a_lobby,
)
from tests.support.sign_in_support import MEMBER, Scene, a_scene

_ADDRESS = "git@heimserver:decks.git"
_JSON = {"accept": "application/json"}
_HAL = "hal-client"
_NOT_FOUND = b'{"type":"about:blank","title":"Not Found","status":404}'
_PROBLEM = "application/problem+json"


def an_attempt(outcome: BuildOutcome, *, failure: str | None = None) -> BuildAttempt:
    return BuildAttempt(
        commit="e91c5ad4f3b2a1908877665544332211aabbccdd",
        started_at=NOW - timedelta(minutes=26),
        outcome=outcome,
        failure=failure,
    )


def a_running_attempt() -> BuildAttempt:
    return an_attempt(BuildOutcome.RUNNING)


def a_failed_attempt(*, said: str | None) -> BuildAttempt:
    return an_attempt(BuildOutcome.FAILED, failure=said)


def a_lobby_with(
    store: FakeDeckStore, *, themes: tuple[str, ...] | None = ("seriph",)
) -> Lobby:
    return a_lobby(
        given=GivenDecks(
            store=store, source=a_configured_source(_ADDRESS), themes=themes
        )
    )


def deck_of(lobby: Lobby, slug: str = SLUG) -> DeckView:
    answer = lobby.client.get(f"/api/decks/{slug}", headers=_JSON)
    assert answer.status_code == HTTPStatus.OK
    assert answer.headers["cache-control"] == "no-store"
    return DeckView.model_validate(answer.json())


def a_built_deck_lobby() -> Lobby:
    lobby = a_lobby_with(a_deck_store(built=a_build()))
    lobby.sign_in()
    return lobby


def test_a_ready_deck_is_its_facts_with_the_built_instant_and_both_views_open() -> None:
    view = deck_of(a_built_deck_lobby())

    assert view.state is DeckState.READY
    assert view.built_at == a_build().built_at
    assert view.attempt is None
    assert view.views_open is True
    assert view.talk == f"{A_TALK_ADDRESS}/deck/{SLUG}/"
    assert view.source == _ADDRESS
    assert view.commit == a_build().commit[:7]
    assert view.themes == ("seriph",)


@pytest.mark.parametrize("themes", [None, ("bricks", "default")])
def test_the_themes_are_the_toolchains_in_its_order_or_nothing_while_unreadable(
    themes: tuple[str, ...] | None,
) -> None:
    lobby = a_lobby_with(a_deck_store(built=a_build()), themes=themes)
    lobby.sign_in()

    assert deck_of(lobby).themes == themes


def test_a_running_build_keeps_the_earlier_talk_but_closes_the_views() -> None:
    attempt = a_running_attempt()
    lobby = a_lobby_with(a_deck_store(built=a_build(), attempt=attempt))
    lobby.sign_in()

    view = deck_of(lobby)

    assert view.state is DeckState.BUILDING
    assert view.built_at == a_build().built_at
    assert view.views_open is False
    assert view.attempt is not None
    assert view.attempt.started_at == attempt.started_at
    assert view.attempt.outcome == attempt.outcome
    assert view.attempt.failure is None


def test_a_first_build_that_runs_has_no_talk_and_no_open_view() -> None:
    lobby = a_lobby_with(a_deck_store(attempt=a_running_attempt()))
    lobby.sign_in()

    view = deck_of(lobby)

    assert view.state is DeckState.BUILDING
    assert view.built_at is None
    assert view.views_open is False


@pytest.mark.parametrize("said", ["slides.md:41:3 broke", "<b>markup</b>", None])
def test_a_failed_build_carries_the_toolchains_words_unchanged_or_none(
    said: str | None,
) -> None:
    attempt: BuildAttempt = a_failed_attempt(said=said)
    lobby = a_lobby_with(a_deck_store(built=a_build(), attempt=attempt))
    lobby.sign_in()

    view = deck_of(lobby)

    assert view.state is DeckState.FAILED
    assert view.attempt is not None
    assert view.attempt.outcome is BuildOutcome.FAILED
    assert view.attempt.failure == said
    assert view.attempt.commit == attempt.commit[:7]
    assert view.built_at == a_build().built_at
    assert view.views_open is True


def test_a_deck_never_built_has_the_sources_commit_and_nothing_delivered() -> None:
    lobby = a_lobby_with(a_deck_store())
    lobby.sign_in()

    view = deck_of(lobby)

    assert view.state is DeckState.NEVER_BUILT
    assert view.built_at is None
    assert view.attempt is None
    assert view.views_open is False
    assert view.commit == a_build().commit[:7]


def test_the_instants_carry_their_offset_as_stored() -> None:
    attempt = a_failed_attempt(said="broke")
    lobby = a_lobby_with(a_deck_store(built=a_build(), attempt=attempt))
    lobby.sign_in()

    body = lobby.client.get(f"/api/decks/{SLUG}", headers=_JSON).json()

    built_at = datetime.fromisoformat(body["built_at"])
    started_at = datetime.fromisoformat(body["attempt"]["started_at"])
    assert built_at == a_build().built_at
    assert built_at.utcoffset() is not None
    assert started_at == attempt.started_at
    assert started_at.utcoffset() is not None


def an_unknown_slug_answer(lobby: Lobby, slug: str = "no-such-deck") -> bytes:
    answer = lobby.client.get(f"/api/decks/{slug}", headers=_JSON)
    assert answer.status_code == HTTPStatus.NOT_FOUND
    assert answer.headers["content-type"] == _PROBLEM
    assert answer.headers["cache-control"] == "no-store"
    return answer.content


def test_the_generated_spec_declares_the_not_found_only_as_a_problem() -> None:
    lobby = a_built_deck_lobby()

    responses = lobby.app.openapi()["paths"]["/api/decks/{slug}"]["get"]["responses"]

    assert list(responses["404"]["content"]) == [_PROBLEM]


def test_an_unknown_slug_is_a_bare_not_found_problem() -> None:
    lobby = a_built_deck_lobby()

    assert an_unknown_slug_answer(lobby) == _NOT_FOUND


@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN])
def test_another_persons_deck_answers_the_unknown_ones_bytes_for_any_role(
    role: Role,
) -> None:
    lobby = a_lobby_with(a_deck_store(built=a_build(), owner=ADMIN))
    lobby.sign_in(username=NEIGHBOUR, role=role)

    assert an_unknown_slug_answer(lobby, SLUG) == _NOT_FOUND


@pytest.mark.parametrize("slug", ["%2e%2e", "%22quoted%22", "a%20space"])
def test_a_slug_the_store_does_not_know_is_the_problem(slug: str) -> None:
    assert an_unknown_slug_answer(a_built_deck_lobby(), slug) == _NOT_FOUND


@pytest.mark.parametrize("path", ["/api/decks/a%2Fb", "/api/decks/x/y"])
def test_an_address_no_route_owns_keeps_the_frameworks_not_found(path: str) -> None:
    lobby = a_built_deck_lobby()

    answer = lobby.client.get(path, headers=_JSON)

    assert answer.status_code == HTTPStatus.NOT_FOUND
    assert answer.headers["content-type"] != _PROBLEM


def test_a_signed_out_caller_meets_the_gate_before_any_not_found() -> None:
    lobby = a_lobby_with(a_deck_store(built=a_build()))

    json_answer = lobby.client.get(f"/api/decks/{SLUG}", headers=_JSON)
    page_answer = lobby.client.get("/api/decks/nope", headers={"accept": "text/html"})

    assert json_answer.status_code == HTTPStatus.UNAUTHORIZED
    assert json_answer.headers["content-type"] != _PROBLEM
    assert page_answer.status_code == HTTPStatus.FOUND


@pytest.mark.parametrize("method", ["post", "put"])
def test_a_write_to_the_deck_is_the_frameworks_method_not_allowed(method: str) -> None:
    lobby = a_built_deck_lobby()

    answer = getattr(lobby.client, method)(f"/api/decks/{SLUG}")

    assert answer.status_code == HTTPStatus.METHOD_NOT_ALLOWED


class _UnreadableDecks(FakeDeckStore):
    def get(self, slug: str) -> None:
        message = f"deck store unavailable for {slug}"
        raise RuntimeError(message)


def test_a_store_that_fails_is_the_plain_server_error_never_a_problem() -> None:
    lobby = a_lobby_with(_UnreadableDecks())
    lobby.sign_in()
    quiet = TestClient(
        lobby.app,
        base_url=str(lobby.client.base_url),
        follow_redirects=False,
        headers=dict(lobby.client.headers),
        raise_server_exceptions=False,
    )
    quiet.cookies.update(lobby.client.cookies)

    answer = quiet.get(f"/api/decks/{SLUG}", headers=_JSON)

    assert answer.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert answer.headers["content-type"] != _PROBLEM
    assert SLUG.encode() not in answer.content


@pytest.fixture
def scene(tmp_path: Path) -> Scene:
    return a_scene(tmp_path, token_clients=frozenset({_HAL}))


def test_a_persons_bearer_reaches_the_route_and_gets_the_problem(scene: Scene) -> None:
    minted = scene.issuer.mint_access_token(
        subject="a-member", groups=MEMBER, authorized_party=_HAL, audience=None
    )

    answer = scene.browser().get(
        f"/api/decks/{SLUG}", headers={"authorization": f"Bearer {minted}", **_JSON}
    )

    assert answer.status_code == HTTPStatus.NOT_FOUND
    assert answer.content == _NOT_FOUND


def test_an_invalid_bearer_beside_the_cookie_is_refused(scene: Scene) -> None:
    browser = scene.browser()
    scene.sign_in(browser)

    answer = browser.get(
        f"/api/decks/{SLUG}", headers={"authorization": "Bearer nonsense", **_JSON}
    )

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert 'error="invalid_token"' in answer.headers["www-authenticate"]
