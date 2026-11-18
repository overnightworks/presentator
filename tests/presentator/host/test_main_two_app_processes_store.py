"""Two app processes with disks of their own serve each other's builds from the store.

What one process built, the other serves from its own copy of the run, and the
sweep of either takes out of the store what no row names any more.
"""

from http import HTTPStatus
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from tests.presentator.adapters.support.build_support import (
    A_TOOLCHAIN_THAT_BUILDS,
    with_the_toolchain,
)
from tests.presentator.host.support.app_process import directory_name_of
from tests.presentator.host.support.app_processes import (
    TALK_HOST,
    App,
    Browser,
    serving_apps,
)
from tests.presentator.host.support.app_requests import (
    AT,
    added,
    admin_of,
    fetched,
    scalar_text,
)
from tests.support.git_remote import EXAMPLE_SLUG, GitRemote
from tests.support.object_store import ObjectStore

_THE_RUN = "SELECT built_run FROM decks"
_THE_TALK = "SELECT active_build FROM decks"
_THE_PDF = "SELECT pdf_export FROM decks"
_TALK_PAGE = f"/deck/{EXAMPLE_SLUG}/"
_PDF = f"/deck/{EXAMPLE_SLUG}/pdf"
_TEXT = f"/api/decks/{EXAMPLE_SLUG}/text"
# A row as a release before the store wrote it: its paths, but no run.
_FORGET_THE_RUN = text("UPDATE decks SET built_run = NULL")


def _runs_on_the_disk_of(app: App, tmp_path: Path) -> set[str]:
    builds = tmp_path / directory_name_of(app.port) / "builds"
    return {entry.name for entry in builds.iterdir()} if builds.is_dir() else set()


def _talk_reader_at(app: App) -> Browser:
    reader = Browser(host=TALK_HOST)
    reader.sign_in(app, "admin", admin=True)
    return reader


@pytest.fixture
def built(environment: pytest.MonkeyPatch, tmp_path: Path, remote: GitRemote) -> None:
    with_the_toolchain(environment, tmp_path, A_TOOLCHAIN_THAT_BUILDS)
    remote.commit_example_deck(at=AT)


@pytest.mark.usefixtures("built")
def test_a_build_one_process_delivered_is_served_by_the_other_and_swept_by_it(
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    remote: GitRemote,
    object_store: ObjectStore,
) -> None:
    with serving_apps(database_url, tmp_path, object_store.environment) as (a, b):
        admin = admin_of(a)
        added(admin, a, "talks", remote)
        fetched(admin, a, "talks")
        first_run = scalar_text(engine, _THE_RUN)
        talk_at_a = (Path(scalar_text(engine, _THE_TALK)) / "index.html").read_bytes()
        pdf_at_a = Path(scalar_text(engine, _THE_PDF)).read_bytes()
        assert first_run not in _runs_on_the_disk_of(b, tmp_path)

        talk = _talk_reader_at(b).request(b, "GET", _TALK_PAGE)
        pdf = admin.request(b, "GET", _PDF)
        text_at_b = admin.request(b, "GET", _TEXT)
        text_at_a = admin.request(a, "GET", _TEXT)

        remote.commit({f"{EXAMPLE_SLUG}/notes.md": "a second push"}, at=AT)
        fetched(admin, b, "talks")
        second_run = scalar_text(engine, _THE_RUN)
        stored_after_b = object_store.runs.names()
        fetched(admin, a, "talks")
        on_a_after_its_sweep = _runs_on_the_disk_of(a, tmp_path)

    assert talk.status_code == HTTPStatus.OK
    assert talk.content == talk_at_a
    assert pdf.status_code == HTTPStatus.OK
    assert pdf.content == pdf_at_a
    assert text_at_b.status_code == HTTPStatus.OK
    assert text_at_b.json()["slides"]
    assert text_at_b.json() == text_at_a.json()
    assert second_run != first_run
    assert stored_after_b == frozenset({second_run})
    assert first_run not in on_a_after_its_sweep


@pytest.mark.usefixtures("built")
def test_a_deck_built_before_the_store_is_rebuilt_by_the_first_refresh_after_it(
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    remote: GitRemote,
    object_store: ObjectStore,
) -> None:
    with serving_apps(database_url, tmp_path, object_store.environment) as (a, b):
        admin = admin_of(a)
        added(admin, a, "talks", remote)
        fetched(admin, a, "talks")
        with engine.begin() as connection:
            connection.execute(_FORGET_THE_RUN)
        before_the_refresh = admin.request(b, "GET", _PDF)

        fetched(admin, b, "talks")
        rebuilt = scalar_text(engine, _THE_RUN)
        after_the_refresh = admin.request(a, "GET", _PDF)

    assert before_the_refresh.status_code == HTTPStatus.NOT_FOUND
    assert object_store.runs.names() == frozenset({rebuilt})
    assert after_the_refresh.status_code == HTTPStatus.OK
