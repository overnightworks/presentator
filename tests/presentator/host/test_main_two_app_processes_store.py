"""Two app processes with disks of their own serve each other's builds from the store.

What one process built, the other serves from its own copy of the run, and the
sweep of either takes out of the store what no row names any more.
"""

from http import HTTPStatus
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from presentator.adapters.postgres.locks import REFRESH_LOCK, parameters
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
_TALK_PAGE = f"/deck/{EXAMPLE_SLUG}/"
_PDF = f"/deck/{EXAMPLE_SLUG}/pdf"
_TEXT = f"/api/decks/{EXAMPLE_SLUG}/text"
# A row as a release before the store wrote it: a build that names no run.
_FORGET_THE_RUN = text("UPDATE decks SET built_run = NULL")
_TAKE_THE_REFRESH_LOCK = text("SELECT pg_advisory_lock(:lock_class, :instance)")


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
        assert first_run not in _runs_on_the_disk_of(b, tmp_path)
        talk_at_a = _talk_reader_at(a).request(a, "GET", _TALK_PAGE)
        pdf_at_a = admin.request(a, "GET", _PDF)

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

    assert talk_at_a.status_code == HTTPStatus.OK
    assert talk.status_code == HTTPStatus.OK
    assert talk.content == talk_at_a.content
    assert pdf_at_a.status_code == HTTPStatus.OK
    assert pdf.status_code == HTTPStatus.OK
    assert pdf.content == pdf_at_a.content
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


@pytest.mark.usefixtures("built")
def test_a_run_gone_from_the_store_serves_nothing_where_no_copy_is_until_rebuilt(
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
        assert object_store.runs.delete(scalar_text(engine, _THE_RUN))

        talk = _talk_reader_at(b).request(b, "GET", _TALK_PAGE)
        pdf = admin.request(b, "GET", _PDF)
        fetched(admin, b, "talks")
        rebuilt = admin.request(b, "GET", _PDF)

    assert talk.status_code == HTTPStatus.NOT_FOUND
    assert pdf.status_code == HTTPStatus.NOT_FOUND
    assert rebuilt.status_code == HTTPStatus.OK


@pytest.mark.usefixtures("built")
def test_a_put_the_store_refuses_ends_the_attempt_failed_and_the_next_refresh_builds(
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    remote: GitRemote,
    object_store: ObjectStore,
) -> None:
    # The first process names a bucket the store does not hold, so it refuses
    # the put; the second names the test's own, the bucket as it exists later.
    missing = {**object_store.environment, "PRESENTATOR_S3_BUCKET": "no-such-bucket"}
    with serving_apps(
        database_url, tmp_path, missing, second_environment=object_store.environment
    ) as (a, b):
        admin = admin_of(a)
        added(admin, a, "talks", remote)
        fetched(admin, a, "talks")
        refused = admin.request(a, "GET", f"/api/decks/{EXAMPLE_SLUG}").json()
        fetched(admin, b, "talks")
        built_run = scalar_text(engine, _THE_RUN)

    assert refused["state"] == "failed"
    assert refused["attempt"]["outcome"] == "failed"
    assert refused["attempt"]["failure"]["reason"] == "store"
    assert object_store.runs.names() == frozenset({built_run})


@pytest.mark.usefixtures("built")
def test_a_process_another_holds_the_refresh_from_drops_its_copy_no_row_names(
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    remote: GitRemote,
    object_store: ObjectStore,
) -> None:
    with (
        serving_apps(database_url, tmp_path, object_store.environment) as (a, b),
        engine.connect() as another_refresh,
    ):
        admin = admin_of(a)
        added(admin, a, "talks", remote)
        fetched(admin, a, "talks")
        superseded = scalar_text(engine, _THE_RUN)
        remote.commit({f"{EXAMPLE_SLUG}/notes.md": "a second push"}, at=AT)
        fetched(admin, b, "talks")
        held_at_a = _runs_on_the_disk_of(a, tmp_path)
        another_refresh.execute(_TAKE_THE_REFRESH_LOCK, parameters(REFRESH_LOCK))

        turned_away = fetched(admin, a, "talks").json()
        left_at_a = _runs_on_the_disk_of(a, tmp_path)

    assert superseded in held_at_a
    assert turned_away["run"] is None
    assert left_at_a == set()
