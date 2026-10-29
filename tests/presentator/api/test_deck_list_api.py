"""`GET /api/decks` lists the reader's own decks, newest changed first."""

from dataclasses import replace
from datetime import datetime, timedelta
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from presentator.api.deck_list_api import DeckListView
from presentator.contracts.decks import (
    Build,
    BuildAttempt,
    BuildOutcome,
    Deck,
    DeckState,
)
from tests.presentator.application.support.fakes import FakeDeckStore
from tests.support.lobby import (
    ADMIN,
    BUILT_TALK,
    EXPORTED_PDF,
    NEIGHBOUR,
    NOW,
    SOURCE_ID,
    GivenDecks,
    Lobby,
    a_configured_source,
    a_lobby,
)
from tests.support.sign_in_support import MEMBER, a_scene

_DECKS = "/api/decks"
_JSON = {"accept": "application/json"}
_COMMIT = "a3f19c2b8d4e5f60718293a4b5c6d7e8f9012345"
_NEIGHBOURS_ID = f"id-of-{NEIGHBOUR}"
_A_SOURCE_URL = "git@heimserver:decks.git"
_HAL = "hal-client"
_SUBJECT = "keycloak-subject-of-a-member"


def a_deck(
    slug: str,
    *,
    owner: str = ADMIN,
    changed_at: datetime = NOW,
    attempt: BuildAttempt | None = None,
    built: bool = False,
) -> Deck:
    return Deck(
        slug=slug,
        title=f"Title of {slug}",
        changed_at=changed_at,
        owner_id=owner,
        source_id=SOURCE_ID,
        commit=_COMMIT,
        build=Build(
            directory=BUILT_TALK, pdf=EXPORTED_PDF, commit=_COMMIT, built_at=NOW
        )
        if built
        else None,
        attempt=attempt,
    )


def an_attempt(outcome: BuildOutcome) -> BuildAttempt:
    return BuildAttempt(
        commit=_COMMIT, started_at=NOW, outcome=outcome, failure=None
    )


def signed_in_lobby(*decks: Deck, sources: tuple[str, ...] = ()) -> Lobby:
    store = FakeDeckStore()
    for deck in decks:
        store.put(deck)
        if deck.build is not None:
            store.put_build(deck.slug, deck.build)
        if deck.attempt is not None:
            store.put_attempt(deck.slug, deck.attempt)
    lobby = a_lobby(
        given=GivenDecks(
            store=store,
            sources=tuple(
                replace(a_configured_source(url), id=f"source-{url}")
                for url in sources
            ),
        )
    )
    lobby.sign_in()
    return lobby


def read(lobby: Lobby) -> DeckListView:
    answer = lobby.client.get(_DECKS, headers=_JSON)
    assert answer.status_code == HTTPStatus.OK
    assert answer.headers["cache-control"] == "no-store"
    return DeckListView.model_validate(answer.json())


def test_a_signed_out_caller_gets_the_gates_401_and_no_deck_data() -> None:
    lobby = a_lobby()

    answer = lobby.client.get(_DECKS, headers=_JSON)

    assert answer.status_code == HTTPStatus.UNAUTHORIZED


def test_a_signed_out_browser_is_sent_to_sign_in() -> None:
    lobby = a_lobby()

    answer = lobby.client.get(_DECKS, headers={"accept": "text/html"})

    assert answer.status_code == HTTPStatus.FOUND
    assert answer.headers["location"].startswith("/login")


def test_a_reader_without_decks_or_source_gets_an_empty_list_and_no_address() -> None:
    view = read(signed_in_lobby())

    assert view.decks == []
    assert view.source_address is None


def test_a_reader_with_one_source_and_no_decks_is_told_its_address() -> None:
    view = read(signed_in_lobby(sources=(_A_SOURCE_URL,)))

    assert view.decks == []
    assert view.source_address == _A_SOURCE_URL


def test_the_address_is_sent_beside_a_list_that_is_not_empty() -> None:
    view = read(signed_in_lobby(a_deck("one"), sources=(_A_SOURCE_URL,)))

    assert len(view.decks) == 1
    assert view.source_address == _A_SOURCE_URL


def test_two_own_sources_name_no_address() -> None:
    view = read(signed_in_lobby(sources=(_A_SOURCE_URL, "git@other:decks.git")))

    assert view.source_address is None


def test_a_deck_is_listed_with_its_slug_title_state_and_stored_instant() -> None:
    changed = NOW - timedelta(days=3)

    view = read(signed_in_lobby(a_deck("one", changed_at=changed)))

    [summary] = view.decks
    assert (summary.slug, summary.title) == ("one", "Title of one")
    assert summary.state is DeckState.NEVER_BUILT
    assert summary.changed_at == changed
    assert summary.changed_at.tzinfo is not None


def test_only_the_readers_own_decks_are_listed_even_for_an_admin() -> None:
    lobby = signed_in_lobby(
        a_deck("mine"), a_deck("hers", owner=_NEIGHBOURS_ID), a_deck("mine-too")
    )

    answer = lobby.client.get(_DECKS, headers=_JSON)

    assert sorted(deck["slug"] for deck in answer.json()["decks"]) == [
        "mine",
        "mine-too",
    ]
    assert "hers" not in answer.text


@pytest.mark.parametrize(
    ("deck", "state"),
    [
        (a_deck("a", built=True), DeckState.READY),
        (a_deck("a", attempt=an_attempt(BuildOutcome.RUNNING)), DeckState.BUILDING),
        (a_deck("a", attempt=an_attempt(BuildOutcome.FAILED)), DeckState.FAILED),
        (a_deck("a"), DeckState.NEVER_BUILT),
    ],
    ids=["ready", "building", "failed", "never-built"],
)
def test_the_state_is_the_decks_one_state(deck: Deck, state: DeckState) -> None:
    [summary] = read(signed_in_lobby(deck)).decks

    assert summary.state is state


def test_newest_change_comes_first_and_equal_instants_order_by_slug() -> None:
    lobby = signed_in_lobby(
        a_deck("zeta"),
        a_deck("old", changed_at=NOW - timedelta(days=1)),
        a_deck("alpha"),
        a_deck("new", changed_at=NOW + timedelta(days=1)),
    )

    first = [deck.slug for deck in read(lobby).decks]
    again = [deck.slug for deck in read(lobby).decks]

    assert first == ["new", "alpha", "zeta", "old"]
    assert again == first


def test_the_wire_carries_no_worded_age_or_state() -> None:
    answer = signed_in_lobby(a_deck("one")).client.get(_DECKS, headers=_JSON)

    [deck] = answer.json()["decks"]
    assert set(deck) == {"slug", "title", "state", "changed_at"}


def test_a_write_to_the_list_is_refused_with_no_store() -> None:
    lobby = signed_in_lobby()

    answer = lobby.client.post(_DECKS, headers=_JSON)

    assert answer.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert answer.headers["cache-control"] == "no-store"


class _BrokenStore(FakeDeckStore):
    def all(self) -> tuple[Deck, ...]:
        message = "the deck store is down"
        raise RuntimeError(message)


def test_a_failing_store_answers_500_without_deck_data() -> None:
    lobby = a_lobby(given=GivenDecks(store=_BrokenStore()))
    lobby.sign_in()
    client = TestClient(
        lobby.app,
        base_url=str(lobby.client.base_url),
        headers=dict(lobby.client.headers),
        cookies=lobby.client.cookies,
        raise_server_exceptions=False,
    )

    answer = client.get(_DECKS, headers=_JSON)

    assert answer.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert "deck store" not in answer.text


def test_a_persons_bearer_reads_that_persons_decks(tmp_path: Path) -> None:
    scene = a_scene(tmp_path, token_clients=frozenset({_HAL}))
    minted = scene.issuer.mint_access_token(
        subject=_SUBJECT, groups=MEMBER, authorized_party=_HAL, audience=None
    )

    answer = scene.browser().get(
        _DECKS, headers={"authorization": f"Bearer {minted}", **_JSON}
    )

    assert answer.status_code == HTTPStatus.OK
    assert DeckListView.model_validate(answer.json()).decks == []
