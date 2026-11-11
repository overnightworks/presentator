"""One deck's page, the talk it leads into, and its PDF, as a browser gets them."""

import shutil
from http import HTTPStatus
from pathlib import Path
from typing import Final
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from presentator.contracts.decks import (
    Build,
    BuildAttempt,
    BuildOutcome,
)
from tests.support.deck_page_support import (
    COMMIT,
    SLUG,
    TITLE,
    a_build,
    a_deck_store,
    a_talk_host,
)
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_TALK_ADDRESS,
    BUILT_TALK,
    EXPORTED_PDF,
    NOW,
    SOURCE_ID,
    GivenDecks,
    a_configured_source,
    a_lobby,
    a_signed_in_lobby,
    a_swapped_build,
)
from tests.support.talk_support import ONLY_THIS_TEST_WROTE_IT, two_hosts

_ADDRESS: Final = "git@heimserver:decks.git"
_PAGE: Final = f"/deck/{SLUG}"
_PROJECTOR: Final = f"/deck/{SLUG}/"
_PRESENTER: Final = f"/deck/{SLUG}/presenter/"
_PDF: Final = f"/deck/{SLUG}/pdf"
_PDF_TYPE: Final = "application/pdf"
_SAVED_AS: Final = f'attachment; filename="{SLUG}.pdf"'
_A_BROWSER: Final = {"accept": "text/html"}
_NO_SUCH_ADDRESS: Final = "/no-such-page"


def assert_the_app_under_not_found(client: TestClient, answer: Response) -> None:
    """The answer is the lobby app's own shell, refused, and hands over no file."""
    shell = client.get(_NO_SUCH_ADDRESS, headers=_A_BROWSER)
    assert answer.status_code == HTTPStatus.NOT_FOUND
    assert answer.headers["content-type"].startswith("text/html")
    assert "content-disposition" not in answer.headers
    assert answer.content == shell.content


@pytest.fixture
def lobby() -> TestClient:
    return a_signed_in_lobby(
        GivenDecks(
            store=a_deck_store(built=a_build()),
            source=a_configured_source(_ADDRESS),
        ),
    )


@pytest.fixture
def unbuilt_talk_host(tmp_path: Path) -> TestClient:
    return a_talk_host(
        tmp_path,
        GivenDecks(
            store=a_deck_store(),
            source=a_configured_source(_ADDRESS),
        ),
    )


@pytest.fixture
def talk_host(tmp_path: Path) -> TestClient:
    return a_talk_host(
        tmp_path,
        GivenDecks(
            store=a_deck_store(built=a_build()),
            source=a_configured_source(_ADDRESS),
        ),
    )


# D4.
def test_a_built_talk_opens_its_presenter_and_its_projector_view_at_the_talk_host(
    talk_host: TestClient,
) -> None:
    application = (BUILT_TALK / "index.html").read_bytes()

    projector = talk_host.get(_PROJECTOR)
    presenter = talk_host.get(_PRESENTER)

    assert projector.status_code == HTTPStatus.OK
    assert presenter.status_code == HTTPStatus.OK
    assert projector.content == application
    assert presenter.content == application


def test_a_talk_is_served_with_the_slide_in_the_address_it_was_left_at(
    talk_host: TestClient,
) -> None:
    application = (BUILT_TALK / "index.html").read_bytes()
    slide = talk_host.get(f"{_PROJECTOR}2")
    presenter_slide = talk_host.get(f"{_PRESENTER}2")

    assert slide.status_code == HTTPStatus.OK
    assert presenter_slide.status_code == HTTPStatus.OK
    assert slide.content == application
    assert presenter_slide.content == application
    assert slide.headers["content-type"].startswith("text/html")


def test_the_download_hands_the_file_the_deck_names_over_to_be_saved(
    lobby: TestClient,
) -> None:
    download = lobby.get(_PDF)

    assert download.status_code == HTTPStatus.OK
    assert download.headers["content-type"] == _PDF_TYPE
    assert download.headers["content-disposition"] == _SAVED_AS
    assert download.content == EXPORTED_PDF.read_bytes()


@pytest.mark.parametrize(
    "built",
    [None, a_build(pdf=Path("/var/lib/presentator/builds/nothing-was-written.pdf"))],
    ids=["nothing built", "a file that is gone"],
)
def test_a_download_with_nothing_behind_it_answers_the_lobby_app_under_not_found(
    built: Build | None,
) -> None:
    signed_in = a_signed_in_lobby(
        GivenDecks(
            store=a_deck_store(built=built),
            source=a_configured_source(_ADDRESS),
        ),
    )

    missing = signed_in.get(_PDF)

    assert_the_app_under_not_found(signed_in, missing)


@pytest.mark.parametrize(
    "slug",
    ["hello\\deck", 'hello"deck'],
    ids=["a separator", "a quote"],
)
def test_a_slug_that_could_reach_a_header_is_refused_before_one_is_built(
    slug: str,
) -> None:
    signed_in = a_signed_in_lobby(
        GivenDecks(
            store=a_deck_store(slug=slug, built=a_build()),
            source=a_configured_source(_ADDRESS),
        ),
    )

    refused = signed_in.get(f"/deck/{quote(slug, safe='')}/pdf")

    assert_the_app_under_not_found(signed_in, refused)


@pytest.mark.parametrize(
    "address", [_PROJECTOR, _PRESENTER], ids=["projector", "presenter"]
)
def test_a_view_of_a_deck_nothing_has_been_built_from_answers_nothing(
    unbuilt_talk_host: TestClient,
    address: str,
) -> None:
    assert unbuilt_talk_host.get(address).status_code == HTTPStatus.NOT_FOUND


@pytest.fixture
def talk_host_with_a_removed_build(tmp_path: Path) -> TestClient:
    """A deck whose row still names a build directory that is gone from disk.

    A request can read the pointer just before a sweep removes the directory it
    names, so a row can outlive its directory; the talk it would deliver must
    still answer not-found, never a traceback.
    """
    talk = tmp_path / "talk"
    talk.mkdir()
    (talk / "index.html").write_text("<h1>a talk</h1>", encoding="utf-8")
    client = a_talk_host(
        tmp_path,
        GivenDecks(
            store=a_deck_store(built=a_build(talk=talk)),
            source=a_configured_source(_ADDRESS),
        ),
    )
    shutil.rmtree(talk)
    return client


@pytest.mark.parametrize(
    "address",
    [_PROJECTOR, _PRESENTER, f"{_PROJECTOR}index.html"],
    ids=["projector", "presenter", "an asset"],
)
def test_a_deck_whose_build_directory_was_removed_answers_nothing(
    talk_host_with_a_removed_build: TestClient,
    address: str,
) -> None:
    """#505 matrix: request during the switch answers 404, never 500."""
    assert (
        talk_host_with_a_removed_build.get(address).status_code == HTTPStatus.NOT_FOUND
    )


@pytest.mark.parametrize(
    ("address", "login_redirect"),
    [
        pytest.param(_PAGE, "/login/oidc?next=%2Fdeck%2Fhello-deck", id="page"),
        pytest.param(
            _PROJECTOR, "/login/oidc?next=%2Fdeck%2Fhello-deck%2F", id="projector"
        ),
        pytest.param(
            _PRESENTER,
            "/login/oidc?next=%2Fdeck%2Fhello-deck%2Fpresenter%2F",
            id="presenter",
        ),
        pytest.param(
            f"{_PROJECTOR}index.html",
            "/login/oidc?next=%2Fdeck%2Fhello-deck%2Findex.html",
            id="an asset",
        ),
        pytest.param(_PDF, "/login/oidc?next=%2Fdeck%2Fhello-deck%2Fpdf", id="the pdf"),
    ],
)
def test_a_deck_address_answers_the_login_to_anyone_who_is_not_signed_in(
    address: str,
    login_redirect: str,
) -> None:
    signed_out = a_lobby(
        given=GivenDecks(
            store=a_deck_store(built=a_build()),
            source=a_configured_source(_ADDRESS),
        ),
    )

    refused = signed_out.client.get(address)

    assert refused.status_code == HTTPStatus.FOUND
    assert refused.headers["location"] == login_redirect
    assert TITLE not in refused.text


def a_secret_a_link_may_point_at(tmp_path: Path) -> Path:
    """A directory holding the secret under every name a build file has."""
    secret = tmp_path / "secret"
    secret.mkdir()
    for name in ("deck.pdf", "app.js", "index.html"):
        (secret / name).write_text(ONLY_THIS_TEST_WROTE_IT, encoding="utf-8")
    return secret


@pytest.mark.parametrize(
    ("replaced", "host", "address"),
    [
        ("deck.pdf", A_BROWSERS_ADDRESS, _PDF),
        ("talk/assets/app.js", A_TALK_ADDRESS, f"{_PROJECTOR}assets/app.js"),
        ("talk/assets", A_TALK_ADDRESS, f"{_PROJECTOR}assets/app.js"),
        ("talk", A_TALK_ADDRESS, f"{_PROJECTOR}assets/app.js"),
        ("talk/index.html", A_TALK_ADDRESS, _PRESENTER),
    ],
    ids=["the pdf", "an asset", "a directory on the way", "the talk", "the page"],
)
def test_a_link_laid_after_the_build_was_adopted_delivers_no_byte_of_its_target(
    tmp_path: Path,
    replaced: str,
    host: str,
    address: str,
) -> None:
    given, run = a_swapped_build(tmp_path, slug=SLUG)
    signed_in = two_hosts(tmp_path, given=given).signed_in_at(host)
    secret = a_secret_a_link_may_point_at(tmp_path)
    target = run / replaced
    if target.is_dir():
        shutil.rmtree(target)
        target.symlink_to(secret)
    else:
        target.unlink()
        target.symlink_to(secret / target.name)

    refused = signed_in.get(address)

    assert ONLY_THIS_TEST_WROTE_IT not in refused.text
    assert refused.headers["content-type"] != _PDF_TYPE
    if replaced.startswith("talk/assets"):
        assert refused.status_code == HTTPStatus.NOT_FOUND
        assert refused.json() == {"detail": HTTPStatus.NOT_FOUND.phrase}


def test_a_pdf_replaced_by_a_link_answers_as_if_it_were_missing(
    tmp_path: Path,
) -> None:
    given, run = a_swapped_build(tmp_path, slug=SLUG)
    signed_in = two_hosts(tmp_path, given=given).signed_in_at(A_BROWSERS_ADDRESS)
    (run / "deck.pdf").unlink()
    (run / "deck.pdf").symlink_to(a_secret_a_link_may_point_at(tmp_path) / "deck.pdf")

    refused = signed_in.get(_PDF)

    assert_the_app_under_not_found(signed_in, refused)


def test_a_deck_removed_by_reconciliation_answers_not_found_until_it_returns(
    tmp_path: Path,
) -> None:
    store = a_deck_store(built=a_build())
    hosts = two_hosts(
        tmp_path,
        given=GivenDecks(store=store, source=a_configured_source(_ADDRESS)),
    )
    signed_in = hosts.signed_in_at(A_BROWSERS_ADDRESS)
    talk_host = hosts.signed_in_at(A_TALK_ADDRESS)

    store.mark_removed_except(present=frozenset(), source_id=SOURCE_ID, at=NOW)
    removed = signed_in.get(_PDF)
    removed_talk = talk_host.get(_PRESENTER)

    store.mark_removed_except(present=frozenset({SLUG}), source_id=SOURCE_ID, at=NOW)
    returned = signed_in.get(_PDF)

    assert_the_app_under_not_found(signed_in, removed)
    assert returned.status_code == HTTPStatus.OK
    assert removed_talk.status_code == HTTPStatus.NOT_FOUND
    assert talk_host.get(_PRESENTER).status_code == HTTPStatus.OK


def an_attempt(outcome: BuildOutcome) -> BuildAttempt:
    """A build that ran after the one that stands, with no words of its own."""
    return BuildAttempt(commit=COMMIT, started_at=NOW, outcome=outcome, failure=None)


# C13.
@pytest.mark.parametrize(
    "outcome", [BuildOutcome.FAILED, BuildOutcome.RUNNING], ids=["failed", "running"]
)
def test_the_last_good_talk_and_its_pdf_stay_open_beside_a_later_attempt(
    tmp_path: Path, outcome: BuildOutcome
) -> None:
    hosts = two_hosts(
        tmp_path,
        given=GivenDecks(
            store=a_deck_store(built=a_build(), attempt=an_attempt(outcome)),
            source=a_configured_source(_ADDRESS),
        ),
    )
    lobby = hosts.signed_in_at(A_BROWSERS_ADDRESS)
    talk_host = hosts.signed_in_at(A_TALK_ADDRESS)

    assert talk_host.get(_PRESENTER).status_code == HTTPStatus.OK
    assert talk_host.get(_PROJECTOR).status_code == HTTPStatus.OK
    assert lobby.get(_PDF).status_code == HTTPStatus.OK
