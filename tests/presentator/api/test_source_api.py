"""The Sources list, a source's page and Fetch now as data, for an admin only."""

import re
from dataclasses import replace
from datetime import timedelta
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from presentator.api.hooks import hook_address
from presentator.api.source_api import SourceListView, SourceView
from presentator.api.sources import CHECK, NEW
from presentator.contracts.decks import (
    MANIFEST_FILE,
    SLIDES_FILE,
    AccessKind,
    Deck,
    DeckFolder,
    Source,
    SourceRun,
    SourceRunFailure,
    SourceRunOutcome,
    SourceState,
)
from tests.presentator.application.support.fakes import (
    LOCAL_MOUNT_EXAMPLE,
    FakeDeckStore,
)
from tests.support.lobby import (
    ADMIN,
    NEIGHBOUR,
    NOW,
    GivenDecks,
    a_configured_source,
    a_lobby,
    a_signed_in_lobby,
)
from tests.support.sign_in_support import ADMIN as ADMIN_GROUPS
from tests.support.sign_in_support import MEMBER, Scene, a_scene

_LIST = "/api/sources"
_PAGE = "/api/sources/{name}"
_FETCH = "/api/sources/{name}/fetch"
_ADDRESS = "git@heimserver:decks.git"
_SSH_ADDRESS = "git@git.example.invalid:talks.git"
_COMMIT = "a3f19c2b8d4e5f60718293a4b5c6d7e8f9012345"
_HAL = "hal-client"
_PROBLEM = "application/problem+json"
_FINGERPRINT = re.compile(r'name="fingerprint"\s+value="([^"]*)"')
_HOOK_SECRET = re.compile(r"data-hook-secret>([^<]+)<")
_JSON = {"accept": "application/json"}
_SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
_OTHER_ID = "the-other-source"
_A_DECK = frozenset({MANIFEST_FILE, SLIDES_FILE})


def a_run(
    source_id: str,
    *,
    ago: timedelta,
    outcome: SourceRunOutcome = SourceRunOutcome.SUCCESS,
    reason: SourceRunFailure | None = None,
) -> SourceRun:
    return SourceRun(
        source_id=source_id,
        at=NOW - ago,
        outcome=outcome,
        commit=None if outcome is SourceRunOutcome.FAILURE else _COMMIT,
        reason=reason,
    )


def a_folder() -> DeckFolder:
    return DeckFolder(
        name="kundenfeedback",
        file_names=_A_DECK,
        title="A talk",
        changed_at=NOW - timedelta(minutes=2),
        commit=_COMMIT,
    )


def a_deck(source: Source, *, owner_id: str, slug: str, title: str) -> Deck:
    return Deck(
        slug=slug,
        title=title,
        changed_at=NOW - timedelta(minutes=2),
        owner_id=owner_id,
        source_id=source.id,
        commit=_COMMIT,
        build=None,
        attempt=None,
    )


def listed(client: TestClient) -> SourceListView:
    answer = client.get(_LIST, headers=_JSON)
    assert answer.status_code == HTTPStatus.OK
    assert answer.headers["cache-control"] == "no-store"
    return SourceListView.model_validate(answer.json())


def shown(client: TestClient, name: str = "decks") -> SourceView:
    answer = client.get(_PAGE.format(name=name), headers=_JSON)
    assert answer.status_code == HTTPStatus.OK
    assert answer.headers["cache-control"] == "no-store"
    return SourceView.model_validate(answer.json())


def a_lobby_of(source: Source, *runs: SourceRun) -> TestClient:
    return a_signed_in_lobby(GivenDecks(source=source, runs=runs))


def test_a_list_of_no_sources_is_empty() -> None:
    assert listed(a_signed_in_lobby()).sources == []


@pytest.mark.parametrize(
    ("outcome", "reason", "state"),
    [
        (SourceRunOutcome.SUCCESS, None, SourceState.REACHABLE),
        (SourceRunOutcome.FAILURE, SourceRunFailure.REFUSED, SourceState.REFUSED),
        (SourceRunOutcome.FAILURE, SourceRunFailure.FAILED, SourceState.FAILED),
        (
            SourceRunOutcome.FAILURE,
            SourceRunFailure.CREDENTIAL_UNRESOLVABLE,
            SourceState.ERROR,
        ),
    ],
)
def test_a_listed_source_says_its_newest_runs_state_and_stored_instant(
    outcome: SourceRunOutcome, reason: SourceRunFailure | None, state: SourceState
) -> None:
    source = a_configured_source(_ADDRESS)
    client = a_lobby_of(
        source,
        a_run(source.id, ago=timedelta(minutes=9)),
        a_run(source.id, ago=timedelta(minutes=3), outcome=outcome, reason=reason),
    )

    row = listed(client).sources[0]

    assert (row.name, row.url, row.access) == ("decks", _ADDRESS, AccessKind.SSH)
    assert row.state is state
    assert row.fetched_at == NOW - timedelta(minutes=3)
    assert row.fetched_at is not None
    assert row.fetched_at.utcoffset() is not None


def test_a_never_fetched_source_has_no_instant() -> None:
    row = listed(a_lobby_of(a_configured_source(_ADDRESS))).sources[0]

    assert row.state is SourceState.NEVER_FETCHED
    assert row.fetched_at is None


def test_a_source_page_has_its_newest_three_runs_newest_first() -> None:
    source = a_configured_source(_ADDRESS)
    client = a_lobby_of(
        source,
        a_run(source.id, ago=timedelta(minutes=30)),
        a_run(
            source.id,
            ago=timedelta(minutes=13),
            outcome=SourceRunOutcome.FAILURE,
            reason=SourceRunFailure.REFUSED,
        ),
        a_run(source.id, ago=timedelta(minutes=8)),
        a_run(source.id, ago=timedelta(minutes=3)),
    )

    view = shown(client)

    assert [run.at for run in view.runs] == [
        NOW - timedelta(minutes=3),
        NOW - timedelta(minutes=8),
        NOW - timedelta(minutes=13),
    ]
    assert view.runs[0].commit == _COMMIT[:7]
    assert view.runs[0].state is SourceState.REACHABLE
    assert view.runs[0].reason is None
    assert view.runs[2].state is SourceState.REFUSED
    assert view.runs[2].reason is SourceRunFailure.REFUSED
    assert view.runs[2].commit is None
    assert view.fetched_at == NOW - timedelta(minutes=3)


@pytest.mark.parametrize(
    ("url", "access"),
    [
        (_ADDRESS, AccessKind.SSH),
        ("https://git.example.invalid/talks.git", AccessKind.HTTPS),
        (f"{LOCAL_MOUNT_EXAMPLE}/talks.git", AccessKind.FILE),
    ],
)
def test_a_source_page_names_the_access_and_carries_no_secret(
    url: str, access: AccessKind
) -> None:
    source = Source(id="s", name="decks", url=url, ref="main", owner_id=ADMIN)

    answer = a_lobby_of(source).get(_PAGE.format(name="decks"), headers=_JSON)

    assert SourceView.model_validate(answer.json()).access is access
    assert set(answer.json()) == set(SourceView.model_fields)
    assert "secret" not in answer.text


def test_an_ssh_source_page_gives_the_deploy_keys_public_half() -> None:
    source = replace(
        a_configured_source(_SSH_ADDRESS), public_key="ssh-ed25519 AAAA-public"
    )

    assert shown(a_lobby_of(source)).public_key == "ssh-ed25519 AAAA-public"


def test_a_source_page_lists_only_the_admins_own_decks_by_title() -> None:
    source = a_configured_source(_ADDRESS)
    store = FakeDeckStore()
    store.put(a_deck(source, owner_id=ADMIN, slug="mine", title="Mine"))
    store.put(a_deck(source, owner_id="someone-else", slug="theirs", title="Theirs"))
    client = a_signed_in_lobby(GivenDecks(source=source, store=store))

    decks = shown(client).decks

    assert [(deck.slug, deck.title) for deck in decks] == [("mine", "Mine")]


def test_the_hook_url_is_absolute_and_names_the_fetch_address() -> None:
    view = shown(a_lobby_of(a_configured_source(_ADDRESS)))

    assert view.hook_url.endswith(hook_address("decks"))
    assert view.hook_url.startswith("http")


def test_the_hook_url_takes_the_scheme_of_the_requests_own_url() -> None:
    lobby = a_lobby(given=GivenDecks(source=a_configured_source(_ADDRESS)))
    lobby.sign_in()
    host = lobby.client.base_url.host

    answer = lobby.client.get(
        f"https://{host}{_PAGE.format(name='decks')}", headers=_JSON
    )

    assert SourceView.model_validate(answer.json()).hook_url == (
        f"https://{lobby.client.base_url.netloc.decode()}{hook_address('decks')}"
    )


def test_the_page_carries_no_one_time_secret_the_jinja_page_still_shows_once() -> None:
    client = a_signed_in_lobby()
    form = {
        "name": "talks",
        "url": "https://git.example.invalid/talks.git",
        "access": "https",
        "secret": "a-read-only-token",
    }
    checked = client.post(CHECK, data=form)
    fingerprint = _FINGERPRINT.search(checked.text)
    assert fingerprint is not None
    created = client.post(NEW, data={**form, "fingerprint": fingerprint.group(1)})

    api_answer = client.get(_PAGE.format(name="talks"), headers=_JSON)
    jinja_page = client.get(created.headers["location"]).text

    secret = _HOOK_SECRET.search(jinja_page)
    assert secret is not None
    assert secret.group(1) not in api_answer.text
    assert secret.group(1) not in client.get(_LIST, headers=_JSON).text
    assert "a-read-only-token" not in api_answer.text


@pytest.mark.parametrize("name", ["no-such-source"])
def test_an_unknown_source_is_a_not_found_problem(name: str) -> None:
    client = a_signed_in_lobby()

    page = client.get(_PAGE.format(name=name), headers=_JSON)
    fetch = client.post(_FETCH.format(name=name), headers=_SAME_ORIGIN)

    for answer in (page, fetch):
        assert answer.status_code == HTTPStatus.NOT_FOUND
        assert answer.headers["content-type"] == _PROBLEM


def test_a_name_that_is_not_a_single_segment_is_the_frameworks_not_found() -> None:
    client = a_signed_in_lobby()

    answer = client.get("/api/sources/a/b", headers=_JSON)

    assert answer.status_code == HTTPStatus.NOT_FOUND
    assert answer.headers["content-type"] != _PROBLEM


def test_fetch_now_answers_no_content_and_records_a_run_and_builds() -> None:
    source = a_configured_source(_ADDRESS)
    client = a_signed_in_lobby(
        GivenDecks(sources=(source,), carried={source.id: (a_folder(),)})
    )

    answer = client.post(_FETCH.format(name="decks"), headers=_SAME_ORIGIN)

    assert answer.status_code == HTTPStatus.NO_CONTENT
    assert answer.headers["cache-control"] == "no-store"
    view = shown(client)
    assert view.state is SourceState.REACHABLE
    assert view.fetched_at == NOW
    assert [deck.slug for deck in view.decks] == ["kundenfeedback"]


def test_a_cross_site_fetch_is_refused_and_fetches_nothing() -> None:
    source = a_configured_source(_ADDRESS)
    client = a_signed_in_lobby(
        GivenDecks(sources=(source,), carried={source.id: (a_folder(),)})
    )

    answer = client.post(
        _FETCH.format(name="decks"), headers={"sec-fetch-site": "cross-site"}
    )

    assert answer.status_code == HTTPStatus.FORBIDDEN
    assert shown(client).state is SourceState.NEVER_FETCHED


@pytest.mark.parametrize("method", ["put", "delete"])
@pytest.mark.parametrize("path", [_LIST, _PAGE.format(name="decks")])
def test_a_write_to_the_reads_is_the_frameworks_method_not_allowed(
    method: str, path: str
) -> None:
    client = a_lobby_of(a_configured_source(_ADDRESS))

    answer = getattr(client, method)(path, headers=_SAME_ORIGIN)

    assert answer.status_code == HTTPStatus.METHOD_NOT_ALLOWED


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", _LIST),
        ("get", _PAGE.format(name="decks")),
        ("post", _FETCH.format(name="decks")),
    ],
)
def test_a_member_is_refused_with_a_problem_and_nothing_is_fetched(
    method: str, path: str
) -> None:
    source = a_configured_source(_ADDRESS)
    lobby = a_lobby(
        given=GivenDecks(sources=(source,), carried={source.id: (a_folder(),)})
    )
    member = lobby.another_browser()
    member.sign_in(username=NEIGHBOUR)
    lobby.sign_in()

    answer = getattr(member.client, method)(path, headers={**_JSON, **_SAME_ORIGIN})

    assert answer.status_code == HTTPStatus.FORBIDDEN
    assert answer.headers["content-type"] == _PROBLEM
    assert shown(lobby.client).state is SourceState.NEVER_FETCHED


def test_a_signed_out_caller_meets_webauth_not_a_problem() -> None:
    lobby = a_lobby(given=GivenDecks(source=a_configured_source(_ADDRESS)))

    reading = lobby.client.get(_LIST, headers=_JSON)
    fetching = lobby.client.post(_FETCH.format(name="decks"))

    assert reading.headers.get("content-type") != _PROBLEM
    assert fetching.headers.get("content-type") != _PROBLEM
    assert reading.status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FOUND}
    assert fetching.status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FOUND}


@pytest.fixture
def scene(tmp_path: Path) -> Scene:
    return a_scene(tmp_path, token_clients=frozenset({_HAL}))


def a_bearer(scene: Scene, groups: list[str]) -> dict[str, str]:
    minted = scene.issuer.mint_access_token(
        subject="a-person", groups=groups, authorized_party=_HAL, audience=None
    )
    return {"authorization": f"Bearer {minted}", **_JSON}


_ROUTES = [
    pytest.param("get", _LIST, id="list"),
    pytest.param("get", _PAGE.format(name="decks"), id="page"),
    pytest.param("post", _FETCH.format(name="decks"), id="fetch"),
]


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        pytest.param("get", _LIST, HTTPStatus.OK, id="list"),
        pytest.param(
            "get", _PAGE.format(name="decks"), HTTPStatus.NOT_FOUND, id="page"
        ),
        pytest.param(
            "post", _FETCH.format(name="decks"), HTTPStatus.NOT_FOUND, id="fetch"
        ),
    ],
)
def test_an_admins_bearer_alone_is_answered_as_the_cookie_is_in_an_empty_instance(
    scene: Scene, method: str, path: str, status: HTTPStatus
) -> None:
    answer = getattr(scene.browser(), method)(
        path, headers={**a_bearer(scene, ADMIN_GROUPS), **_SAME_ORIGIN}
    )

    assert answer.status_code == status
    if status is HTTPStatus.OK:
        assert SourceListView.model_validate(answer.json()).sources == []
    else:
        assert answer.headers["content-type"] == _PROBLEM


@pytest.mark.parametrize(("method", "path"), _ROUTES)
def test_a_members_bearer_is_refused_with_a_problem(
    scene: Scene, method: str, path: str
) -> None:
    answer = getattr(scene.browser(), method)(
        path, headers={**a_bearer(scene, MEMBER), **_SAME_ORIGIN}
    )

    assert answer.status_code == HTTPStatus.FORBIDDEN
    assert answer.headers["content-type"] == _PROBLEM


@pytest.mark.parametrize(("method", "path"), _ROUTES)
def test_an_invalid_bearer_beside_an_admin_cookie_is_refused(
    scene: Scene, method: str, path: str
) -> None:
    browser = scene.browser()
    scene.sign_in(browser, groups=ADMIN_GROUPS)

    answer = getattr(browser, method)(
        path, headers={"authorization": "Bearer nonsense", **_JSON, **_SAME_ORIGIN}
    )

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert 'error="invalid_token"' in answer.headers["www-authenticate"]


class _UnreadableDecks(FakeDeckStore):
    def all(self) -> tuple[Deck, ...]:
        message = "deck store unavailable"
        raise RuntimeError(message)


def test_a_store_that_fails_is_the_plain_server_error_with_no_source_data() -> None:
    source = a_configured_source(_ADDRESS)
    lobby = a_lobby(given=GivenDecks(source=source, store=_UnreadableDecks()))
    lobby.sign_in()
    quiet = TestClient(
        lobby.app,
        base_url=str(lobby.client.base_url),
        follow_redirects=False,
        headers=dict(lobby.client.headers),
        cookies=lobby.client.cookies,
        raise_server_exceptions=False,
    )

    answer = quiet.get(_PAGE.format(name="decks"), headers=_JSON)

    assert answer.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert answer.headers["content-type"] != _PROBLEM
    assert source.url not in answer.text
