"""Two app processes on one Postgres keep the guarantees of every shared write.

Each case sends the same kind of request to both processes at once, as a load
balancer does, and judges the one database and what either process answers.
"""

import stat
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from threading import Barrier

import httpx2
import pytest
from pydantic import JsonValue
from sqlalchemy import Engine, text

from presentator.application.decks import SourceRefusal
from presentator.contracts.decks import RECENT_SOURCE_RUNS
from tests.presentator.host.support.app_processes import (
    KEY_DRAFT,
    App,
    Browser,
    serving_apps,
)
from tests.support.git_remote import EXAMPLE_SLUG, GitRemote, a_path_carrying
from tests.support.postgres import engine_on

_AT = datetime(2026, 11, 14, 12, tzinfo=UTC)
_SOURCES = "/api/sources"
_CHECK = "/api/sources/new/check"
_DECKS = "/api/decks"
_SETTINGS = "/api/settings"
_ACCOUNT = "/api/account"
_ROUNDS = RECENT_SOURCE_RUNS + 1
# Stands in for the ssh git starts: it runs the upload-pack the remote command
# names on this machine, so an scp-form address reaches a repository on disk.
_SSH_ON_THIS_MACHINE = """#!/bin/sh
for command; do :; done
exec sh -c "git ${command#git-}"
"""


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    each = engine_on(database_url)
    try:
        yield each
    finally:
        each.dispose()


@pytest.fixture
def remotes_over_ssh(
    environment: pytest.MonkeyPatch,
    tmp_path: Path,
    remote: GitRemote,
    another_remote: GitRemote,
) -> tuple[GitRemote, GitRemote]:
    """Two repositories git reaches through an ssh that runs on this machine."""
    ssh = tmp_path / "programs" / "ssh"
    ssh.parent.mkdir()
    ssh.write_text(_SSH_ON_THIS_MACHINE, encoding="utf-8")
    ssh.chmod(ssh.stat().st_mode | stat.S_IEXEC)
    environment.setenv("PATH", a_path_carrying(ssh.parent))
    for each in (remote, another_remote):
        each.commit({"README.md": "talks"}, at=_AT)
    return remote, another_remote


def _scalar(engine: Engine, statement: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(statement)).scalar_one())


def _text(engine: Engine, statement: str) -> str:
    with engine.connect() as connection:
        return str(connection.execute(text(statement)).scalar_one())


def _at_once[Answer](calls: list[Callable[[], Answer]]) -> list[Answer]:
    start = Barrier(len(calls))

    def go(call: Callable[[], Answer]) -> Answer:
        start.wait()
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return [each.result() for each in [pool.submit(go, call) for call in calls]]


def _admin(app: App) -> Browser:
    admin = Browser()
    admin.sign_in(app, "admin", admin=True)
    return admin


def _checked(admin: Browser, app: App, **fields: JsonValue) -> dict[str, JsonValue]:
    """The body of a source whose address the app has probed."""
    body: dict[str, JsonValue] = {"secret": None, "key_draft_id": None, **fields}
    answer = admin.request(app, "POST", _CHECK, body=body)
    assert answer.status_code == HTTPStatus.OK, answer.text
    return {**body, "fingerprint": answer.json()["fingerprint"]}


def _create(admin: Browser, app: App, body: dict[str, JsonValue]) -> httpx2.Response:
    return admin.request(app, "POST", _SOURCES, body=body)


def _added(admin: Browser, app: App, name: str, remote: GitRemote) -> None:
    created = _create(admin, app, _checked(admin, app, name=name, url=remote.url))
    assert created.status_code == HTTPStatus.CREATED, created.text


def _fetch(admin: Browser, app: App, name: str) -> httpx2.Response:
    answer = admin.request(app, "POST", f"{_SOURCES}/{name}/fetch")
    assert answer.status_code == HTTPStatus.OK, answer.text
    return answer


def _listed_slugs(admin: Browser, app: App) -> list[str]:
    answer = admin.request(app, "GET", _DECKS)
    assert answer.status_code == HTTPStatus.OK
    return [deck["slug"] for deck in answer.json()["decks"]]


def _one_created_one_refused(answers: list[httpx2.Response]) -> None:
    assert sorted(each.status_code for each in answers) == [
        HTTPStatus.CREATED,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ], [each.text for each in answers]
    refused = [each.json()["reason"] for each in answers if each.is_client_error]
    # The loser is told the name or the address is taken, by the pre-check when
    # the winner was already stored, else by the store.
    assert len(refused) == 1
    assert refused[0] in {SourceRefusal.DUPLICATE_NAME, SourceRefusal.DUPLICATE_URL}


@pytest.mark.usefixtures("environment")
@pytest.mark.parametrize(
    "second_name", ["talks", "other-talks"], ids=["one name", "one address"]
)
def test_a_source_created_at_both_processes_at_once_is_stored_once(
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    remote: GitRemote,
    second_name: str,
) -> None:
    remote.commit({"README.md": "talks"}, at=_AT)
    with serving_apps(database_url, tmp_path) as apps:
        admin = _admin(apps[0])
        first = _checked(admin, apps[0], name="talks", url=remote.url)
        second = {**first, "name": second_name}

        answers = _at_once(
            [
                lambda: _create(admin, apps[0], first),
                lambda: _create(admin, apps[1], second),
            ]
        )

    _one_created_one_refused(answers)
    assert _scalar(engine, "SELECT count(*) FROM sources") == 1


@pytest.mark.usefixtures("environment")
def test_a_key_draft_promoted_at_both_processes_at_once_makes_one_source(
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    remotes_over_ssh: tuple[GitRemote, GitRemote],
) -> None:
    with serving_apps(database_url, tmp_path) as apps:
        admin = _admin(apps[0])
        draft = admin.request(apps[0], "POST", KEY_DRAFT).json()["id"]
        bodies = [
            _checked(
                admin,
                apps[0],
                name=name,
                url=f"git@localhost:{each.bare}",
                key_draft_id=draft,
            )
            for name, each in zip(("first", "second"), remotes_over_ssh, strict=True)
        ]

        answers = _at_once(
            [
                lambda: _create(admin, apps[0], bodies[0]),
                lambda: _create(admin, apps[1], bodies[1]),
            ]
        )

    _one_created_one_refused(answers)
    assert _scalar(engine, "SELECT count(*) FROM sources") == 1
    assert _scalar(engine, "SELECT count(*) FROM source_key_drafts") == 0


@pytest.mark.usefixtures("environment")
def test_runs_recorded_at_both_processes_at_once_keep_exactly_the_bound(
    database_url: str, tmp_path: Path, engine: Engine, remote: GitRemote
) -> None:
    remote.commit({"README.md": "talks"}, at=_AT)
    with serving_apps(database_url, tmp_path) as apps:
        admin = _admin(apps[0])
        _added(admin, apps[0], "talks", remote)

        for _ in range(_ROUNDS):
            answers = _at_once(
                [lambda app=app: _fetch(admin, app, "talks") for app in apps]
            )
            assert all(each.json()["run"] is not None for each in answers)

    assert _scalar(engine, "SELECT count(*) FROM source_runs") == RECENT_SOURCE_RUNS
    assert _ROUNDS * len(apps) > RECENT_SOURCE_RUNS


@pytest.mark.usefixtures("environment")
def test_two_sources_carrying_one_deck_fetched_at_both_processes_give_it_to_one(
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    remote: GitRemote,
    another_remote: GitRemote,
) -> None:
    for each in (remote, another_remote):
        each.commit_example_deck(at=_AT)
    carrier = "SELECT s.name FROM decks d JOIN sources s ON s.id = d.source_id"
    with serving_apps(database_url, tmp_path) as apps:
        admin = _admin(apps[0])
        _added(admin, apps[0], "first", remote)
        _added(admin, apps[0], "second", another_remote)

        _at_once(
            [
                lambda: _fetch(admin, apps[0], "first"),
                lambda: _fetch(admin, apps[1], "second"),
            ]
        )
        keeper = _text(engine, carrier)
        listed = [_listed_slugs(admin, app) for app in apps]
        _fetch(admin, apps[1], ({"first", "second"} - {keeper}).pop())

    assert _scalar(engine, "SELECT count(*) FROM decks") == 1
    assert listed == [[EXAMPLE_SLUG], [EXAMPLE_SLUG]]
    assert _text(engine, carrier) == keeper


@pytest.mark.usefixtures("environment")
def test_a_deck_removed_upstream_is_marked_by_fetches_at_both_processes(
    database_url: str, tmp_path: Path, engine: Engine, remote: GitRemote
) -> None:
    remote.commit_example_deck(at=_AT)
    with serving_apps(database_url, tmp_path) as apps:
        admin = _admin(apps[0])
        _added(admin, apps[0], "talks", remote)
        _fetch(admin, apps[0], "talks")
        assert _listed_slugs(admin, apps[1]) == [EXAMPLE_SLUG]
        remote.remove(EXAMPLE_SLUG, at=_AT)

        _at_once([lambda app=app: _fetch(admin, app, "talks") for app in apps])

        listed = [_listed_slugs(admin, app) for app in apps]

    assert listed == [[], []]
    assert _scalar(engine, "SELECT count(*) FROM decks WHERE removed_at IS NULL") == 0
    assert _scalar(engine, "SELECT count(*) FROM decks") == 1


@pytest.mark.usefixtures("environment")
def test_settings_and_a_theme_saved_at_both_processes_at_once_leave_one_row_each(
    database_url: str, tmp_path: Path, engine: Engine
) -> None:
    names = ["Atelier North", "Atelier South"]
    themes = ["light", "dark"]
    with serving_apps(database_url, tmp_path) as apps:
        admin = _admin(apps[0])

        answers = _at_once(
            [
                lambda: admin.request(
                    apps[0], "PUT", _SETTINGS, body={"name": names[0]}
                ),
                lambda: admin.request(
                    apps[1], "PUT", _SETTINGS, body={"name": names[1]}
                ),
                lambda: admin.request(
                    apps[0], "PUT", _ACCOUNT, body={"theme": themes[0]}
                ),
                lambda: admin.request(
                    apps[1], "PUT", _ACCOUNT, body={"theme": themes[1]}
                ),
            ]
        )

        read_names = [
            admin.request(app, "GET", _SETTINGS).json()["name"] for app in apps
        ]
        read_themes = [
            admin.request(app, "GET", _ACCOUNT).json()["theme"] for app in apps
        ]

    assert [each.status_code for each in answers] == [HTTPStatus.OK] * 4
    assert _scalar(engine, "SELECT count(*) FROM instance_settings") == 1
    assert _scalar(engine, "SELECT count(*) FROM person_preferences") == 1
    assert read_names[0] == read_names[1]
    assert read_names[0] in names
    assert read_themes[0] == read_themes[1]
    assert read_themes[0] in themes
