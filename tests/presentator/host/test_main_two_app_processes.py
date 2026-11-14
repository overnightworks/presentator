"""Two app processes on one Postgres share sign-ins, sources and decks."""

from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import text

from presentator.adapters.postgres.migrate import BEHIND_THIS_RELEASE, NO_REVISION
from tests.presentator.host.support.app_processes import (
    KEY_DRAFT,
    App,
    Browser,
    serving_apps,
    starting_apps,
)
from tests.presentator.host.support.revision_history import a_longer_history
from tests.support.postgres import engine_on

_A_REVISION_ONLY_A_NEWER_RELEASE_KNOWS = "9999"
_PEOPLE = 6


def _scalar(database_url: str, statement: str) -> int:
    engine = engine_on(database_url)
    try:
        with engine.connect() as connection:
            return int(connection.execute(text(statement)).scalar_one())
    finally:
        engine.dispose()


def _replace_the_revision(database_url: str, revision: str | None) -> None:
    engine = engine_on(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM alembic_version"))
            if revision is not None:
                connection.execute(
                    text("INSERT INTO alembic_version VALUES (:revision)"),
                    {"revision": revision},
                )
    finally:
        engine.dispose()


@pytest.mark.usefixtures("environment")
def test_a_sign_in_at_one_process_is_valid_at_the_other_until_a_sign_out_ends_it(
    database_url: str, tmp_path: Path
) -> None:
    with serving_apps(database_url, tmp_path) as (first, second):
        person = Browser()
        person.sign_in(first, "alice")
        cookies_of_the_session = dict(person.cookies)

        at_the_other_process = person.signed_in_as(second)
        person.sign_out(second)
        replayed_at_the_first = Browser(cookies_of_the_session).signed_in_as(first)

    assert at_the_other_process == "alice"
    assert replayed_at_the_first is None
    assert not (tmp_path / "presentator.sqlite3").exists()


@pytest.mark.usefixtures("environment")
def test_the_same_people_signing_in_at_both_processes_at_once_get_one_account_each(
    database_url: str, tmp_path: Path
) -> None:
    people = [f"person-{number}" for number in range(_PEOPLE)]
    with serving_apps(database_url, tmp_path) as apps:
        start = Barrier(len(apps) * len(people))

        def sign_in(app: App, subject: str) -> str | None:
            browser = Browser()
            start.wait()
            browser.sign_in(app, subject)
            return browser.signed_in_as(app)

        with ThreadPoolExecutor(max_workers=len(apps) * len(people)) as pool:
            signed_in = [
                pool.submit(sign_in, app, subject) for subject in people for app in apps
            ]

    assert [each.result() for each in signed_in] == [
        subject for subject in people for _ in apps
    ]
    assert _scalar(database_url, "SELECT count(*) FROM users") == len(people)
    assert _scalar(database_url, "SELECT count(*) FROM sessions") == 2 * len(people)


@pytest.mark.usefixtures("environment")
def test_an_admin_opening_add_at_both_processes_at_once_is_given_one_draft_key(
    database_url: str, tmp_path: Path
) -> None:
    with serving_apps(database_url, tmp_path) as apps:
        admin = Browser()
        admin.sign_in(apps[0], "admin", admin=True)
        start = Barrier(len(apps))

        def open_add(app: App) -> dict[str, str]:
            start.wait()
            answer = admin.request(app, "POST", KEY_DRAFT)
            assert answer.status_code == HTTPStatus.OK
            return answer.json()

        with ThreadPoolExecutor(max_workers=len(apps)) as pool:
            drafts = [
                each.result() for each in [pool.submit(open_add, a) for a in apps]
            ]

    assert drafts[0] == drafts[1]
    assert _scalar(database_url, "SELECT count(*) FROM source_key_drafts") == 1


@pytest.mark.usefixtures("environment")
def test_an_empty_database_is_refused_at_start_by_both_processes(
    unmigrated_database_url: str, tmp_path: Path
) -> None:
    with starting_apps(unmigrated_database_url, tmp_path) as (processes, _ports):
        finished = processes.finish()

    assert all(each.exit_code != 0 for each in finished), finished
    assert all(NO_REVISION in each.output for each in finished), finished
    assert (
        _scalar(
            unmigrated_database_url,
            "SELECT count(*) FROM information_schema.tables"
            " WHERE table_schema = 'public'",
        )
        == 0
    )


@pytest.mark.usefixtures("environment")
def test_an_older_database_is_refused_at_start_by_both_processes(
    database_url: str, tmp_path: Path
) -> None:
    longer_history = a_longer_history(tmp_path)

    with starting_apps(database_url, tmp_path, history=longer_history) as (
        processes,
        _ports,
    ):
        finished = processes.finish()

    assert all(each.exit_code != 0 for each in finished), finished
    assert all(BEHIND_THIS_RELEASE in each.output for each in finished), finished


@pytest.mark.usefixtures("environment")
def test_a_newer_database_is_served(database_url: str, tmp_path: Path) -> None:
    _replace_the_revision(database_url, _A_REVISION_ONLY_A_NEWER_RELEASE_KNOWS)

    with serving_apps(database_url, tmp_path) as apps:
        statuses = [app.request("GET", "/health", {}).status_code for app in apps]

    assert statuses == [HTTPStatus.OK, HTTPStatus.OK]


@pytest.mark.usefixtures("environment")
def test_readiness_follows_the_revision_the_database_holds(
    database_url: str, tmp_path: Path
) -> None:
    with serving_apps(database_url, tmp_path) as apps:
        _replace_the_revision(database_url, None)
        unready = [app.request("GET", "/health", {}).status_code for app in apps]

    assert unready == [HTTPStatus.SERVICE_UNAVAILABLE] * len(apps)
