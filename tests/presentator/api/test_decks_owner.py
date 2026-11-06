"""A deck exists only for the account that owns its source."""

import shutil
from dataclasses import dataclass, replace
from http import HTTPStatus
from pathlib import Path
from typing import Final

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from presentator.contracts.decks import Build, Deck, Source
from presentator.contracts.models import Role, User
from tests.presentator.api.support import source_api_support
from tests.presentator.application.support.fakes import FakeBuildRunner, FakeDeckStore
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_BROWSERS_HEADERS,
    A_TALK_ADDRESS,
    ADMIN,
    AN_ASSET,
    BUILT_TALK,
    EXPORTED_PDF,
    NEIGHBOUR,
    NOW,
    SOURCE_ID,
    GivenDecks,
    a_configured_source,
    a_lobby,
)
from tests.support.lobby_reads import deck_list, listed_titles
from tests.support.talk_support import cookies_without_values, two_hosts

_COMMIT: Final = "a3f19c2b8d4e5f60718293a4b5c6d7e8f9012345"
_B: Final = f"id-of-{NEIGHBOUR}"
_A_SOURCE_URL: Final = "git@heimserver:decks.git"
_B_SOURCE_URL: Final = "https://git.example.invalid/b-talks.git"
_THE_UNKNOWN: Final = "no-deck"


def _b_source() -> Source:
    return replace(
        a_configured_source(_B_SOURCE_URL),
        id="the-source-of-b",
        name="b-talks",
        owner_id=_B,
    )


def _store_of_built_decks(
    talk: Path = BUILT_TALK, pdf: Path = EXPORTED_PDF
) -> FakeDeckStore:
    store = FakeDeckStore()
    for slug, owner, source_id in (
        ("a-deck", ADMIN, SOURCE_ID),
        ("b-deck", _B, "the-source-of-b"),
    ):
        store.put(
            Deck(
                slug=slug,
                title=f"Title of {slug}",
                changed_at=NOW,
                owner_id=owner,
                source_id=source_id,
                commit=_COMMIT,
                build=None,
                attempt=None,
            ),
        )
        store.put_build(
            slug,
            Build(directory=talk, pdf=pdf, commit=_COMMIT, built_at=NOW),
        )
    return store


@dataclass(frozen=True, slots=True)
class _TwoAdmins:
    """Both admins, each signed in at the lobby and at the talk host."""

    as_a: TestClient
    as_b: TestClient
    talk_as_a: TestClient
    talk_as_b: TestClient

    def as_a_at(self, origin: str) -> TestClient:
        """The browser of `a` at the lobby or at the talk host."""
        return self.as_a if origin == A_BROWSERS_ADDRESS else self.talk_as_a


_ADMIN_B: Final = User(id=_B, username=NEIGHBOUR, role=Role.ADMIN)


def _two_admins(tmp_path: Path, *, stuck_mirror: bool = False) -> _TwoAdmins:
    foreign = _b_source()
    run = tmp_path / "builds" / "run" / "out"
    shutil.copytree(BUILT_TALK, run / "talk")
    shutil.copyfile(EXPORTED_PDF, run / "deck.pdf")
    hosts = two_hosts(
        tmp_path,
        given=GivenDecks(
            builder=FakeBuildRunner(root=tmp_path / "builds"),
            store=_store_of_built_decks(run / "talk", run / "deck.pdf"),
            source=foreign if stuck_mirror else None,
            sources=(a_configured_source(_A_SOURCE_URL), foreign),
            stuck_mirror=stuck_mirror,
        ),
    )
    return _TwoAdmins(
        as_a=hosts.signed_in_at(A_BROWSERS_ADDRESS, headers=A_BROWSERS_HEADERS),
        as_b=hosts.signed_in_at(
            A_BROWSERS_ADDRESS, _ADMIN_B, headers=A_BROWSERS_HEADERS
        ),
        talk_as_a=hosts.signed_in_at(A_TALK_ADDRESS),
        talk_as_b=hosts.signed_in_at(A_TALK_ADDRESS, _ADMIN_B),
    )


def _alike(answer: Response, unknown: Response) -> None:
    def comparable(response: Response) -> dict[str, str]:
        return {
            name: value
            for name, value in response.headers.items()
            if name not in {"date", "set-cookie"}
        }

    assert answer.status_code == unknown.status_code
    assert answer.content == unknown.content
    assert comparable(answer) == comparable(unknown)
    assert cookies_without_values(answer) == cookies_without_values(unknown)


_NO_HEADERS: Final[dict[str, str]] = {}
_REQUESTS: Final = [
    pytest.param(A_TALK_ADDRESS, "get", "/", _NO_HEADERS, id="the-projector"),
    pytest.param(A_TALK_ADDRESS, "get", "/presenter/", _NO_HEADERS, id="the-presenter"),
    pytest.param(A_TALK_ADDRESS, "get", f"/{AN_ASSET}", _NO_HEADERS, id="an-asset"),
    pytest.param(
        A_TALK_ADDRESS, "get", "/no-such-file", _NO_HEADERS, id="the-fallback"
    ),
    pytest.param(A_TALK_ADDRESS, "head", "/presenter/", _NO_HEADERS, id="head"),
    pytest.param(
        A_TALK_ADDRESS,
        "get",
        f"/{AN_ASSET}",
        {"range": "bytes=0-9"},
        id="a-range",
    ),
    pytest.param(A_BROWSERS_ADDRESS, "get", "/pdf", _NO_HEADERS, id="the-pdf"),
]


# DK7
@pytest.mark.parametrize(("origin", "method", "tail", "headers"), _REQUESTS)
def test_a_deck_of_another_account_answers_like_an_unknown_slug(
    tmp_path: Path,
    origin: str,
    method: str,
    tail: str,
    headers: dict[str, str],
) -> None:
    admins = _two_admins(tmp_path)
    send = getattr(admins.as_a_at(origin), method)

    foreign = send(f"/deck/b-deck{tail}", headers=headers)
    unknown = send(f"/deck/{_THE_UNKNOWN}{tail}", headers=headers)

    _alike(foreign, unknown)
    assert foreign.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize("header", ["if-none-match", "if-modified-since"])
def test_a_conditional_request_for_a_foreign_asset_gets_no_not_modified(
    tmp_path: Path,
    header: str,
) -> None:
    admins = _two_admins(tmp_path)
    own = admins.talk_as_b.get(f"/deck/b-deck/{AN_ASSET}")
    validators = {
        "if-none-match": own.headers["etag"],
        "if-modified-since": "Fri, 01 Jan 2100 00:00:00 GMT",
    }

    foreign = admins.talk_as_a.get(
        f"/deck/b-deck/{AN_ASSET}",
        headers={header: validators[header]},
    )
    unknown = admins.talk_as_a.get(
        f"/deck/{_THE_UNKNOWN}/{AN_ASSET}",
        headers={header: validators[header]},
    )

    _alike(foreign, unknown)
    assert foreign.status_code == HTTPStatus.NOT_FOUND


def test_the_owner_receives_every_shape_of_request_for_the_own_deck(
    tmp_path: Path,
) -> None:
    admins = _two_admins(tmp_path)
    client = admins.talk_as_a
    asset = client.get(f"/deck/a-deck/{AN_ASSET}")

    assert asset.status_code == HTTPStatus.OK
    assert asset.content == (BUILT_TALK / AN_ASSET).read_bytes()
    assert (
        client.get("/deck/a-deck/no-such-file").content
        == (BUILT_TALK / "index.html").read_bytes()
    )
    head = client.head("/deck/a-deck/presenter/")
    assert head.status_code == HTTPStatus.OK
    assert head.content == b""
    part = client.get(f"/deck/a-deck/{AN_ASSET}", headers={"range": "bytes=0-9"})
    assert part.status_code == HTTPStatus.PARTIAL_CONTENT
    again = client.get(
        f"/deck/a-deck/{AN_ASSET}",
        headers={"if-none-match": asset.headers["etag"]},
    )
    assert again.status_code == HTTPStatus.NOT_MODIFIED
    assert admins.as_a.get("/deck/a-deck/pdf").status_code == HTTPStatus.OK


# DK6
def test_each_admin_lists_exactly_the_own_deck(tmp_path: Path) -> None:
    admins = _two_admins(tmp_path)

    assert listed_titles(admins.as_a) == ["Title of a-deck"]
    assert listed_titles(admins.as_b) == ["Title of b-deck"]


# DK8
def test_an_admin_with_no_deck_gets_an_empty_list_that_names_no_address() -> None:
    store = _store_of_built_decks()
    del store.kept["a-deck"]
    lobby = a_lobby(given=GivenDecks(store=store, source=_b_source()))
    lobby.sign_in()

    listed = deck_list(lobby.client)

    assert listed.decks == []
    assert listed.source_address is None
    assert _B_SOURCE_URL not in lobby.client.get("/api/decks").text


_WALTER: Final = User(id="id-of-walter", username="walter", role=Role.USER)
_AT_THE_LOBBY_OR_THE_TALK_HOST: Final = [
    (A_TALK_ADDRESS, "/presenter/"),
    (A_BROWSERS_ADDRESS, "/pdf"),
]


# DK9
def test_a_member_without_the_admin_role_has_no_deck_of_another_account(
    tmp_path: Path,
) -> None:
    hosts = two_hosts(tmp_path, given=GivenDecks(store=_store_of_built_decks()))

    assert listed_titles(hosts.signed_in_at(A_BROWSERS_ADDRESS, _WALTER)) == []
    for origin, tail in _AT_THE_LOBBY_OR_THE_TALK_HOST:
        member = hosts.signed_in_at(origin, _WALTER)
        _alike(
            member.get(f"/deck/b-deck{tail}"),
            member.get(f"/deck/{_THE_UNKNOWN}{tail}"),
        )
    member = hosts.signed_in_at(A_BROWSERS_ADDRESS, _WALTER)
    _alike(member.get("/api/decks/b-deck"), member.get(f"/api/decks/{_THE_UNKNOWN}"))


# DK11
def test_the_owner_without_the_admin_role_has_the_own_deck_but_no_sources(
    tmp_path: Path,
) -> None:
    store = _store_of_built_decks()
    store.put(replace(store.kept["a-deck"], owner_id=_WALTER.id))
    hosts = two_hosts(tmp_path, given=GivenDecks(store=store))
    client = hosts.signed_in_at(A_BROWSERS_ADDRESS, _WALTER)

    assert listed_titles(client) == ["Title of a-deck"]
    for origin, tail in _AT_THE_LOBBY_OR_THE_TALK_HOST:
        member = hosts.signed_in_at(origin, _WALTER)
        assert member.get(f"/deck/a-deck{tail}").status_code == HTTPStatus.OK
    assert client.get("/api/decks/a-deck").status_code == HTTPStatus.OK
    assert (
        client.get(
            source_api_support.LIST, headers=source_api_support.ASKING_FOR_JSON
        ).status_code
        == HTTPStatus.FORBIDDEN
    )


# DK10
def test_a_deck_of_a_removed_source_answers_like_an_unknown_slug_for_its_owner(
    tmp_path: Path,
) -> None:
    admins = _two_admins(tmp_path)
    own_source = a_configured_source(_A_SOURCE_URL)

    counts = source_api_support.preview_removal(admins.as_a, own_source.name).json()
    removed = source_api_support.remove(
        admins.as_a,
        own_source.name,
        deck_count=counts["deck_count"],
        run_count=counts["run_count"],
    )

    assert removed.status_code == HTTPStatus.NO_CONTENT
    for talk_host in (admins.talk_as_a, admins.talk_as_b):
        _alike(
            talk_host.get("/deck/a-deck/presenter/"),
            talk_host.get(f"/deck/{_THE_UNKNOWN}/presenter/"),
        )
    for client in (admins.as_a, admins.as_b):
        _alike(
            client.get("/api/decks/a-deck"),
            client.get(f"/api/decks/{_THE_UNKNOWN}"),
        )
        _alike(client.get("/deck/a-deck/pdf"), client.get(f"/deck/{_THE_UNKNOWN}/pdf"))


def test_a_refused_removal_of_anothers_source_shows_no_deck_titles(
    tmp_path: Path,
) -> None:
    admins = _two_admins(tmp_path, stuck_mirror=True)
    counts = source_api_support.preview_removal(admins.as_a, "b-talks").json()

    refused = source_api_support.remove(
        admins.as_a,
        "b-talks",
        deck_count=counts["deck_count"],
        run_count=counts["run_count"],
    )

    assert refused.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert "b-deck" not in refused.text
