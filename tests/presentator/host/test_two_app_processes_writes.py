"""Two app processes on one Postgres keep the guarantees of every shared write.

Each case sends the same kind of request to both processes at once, as a load
balancer does, and judges the one database and what either process answers.
"""

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
from presentator.contracts.decks import RECENT_SOURCE_RUNS, SourceRunOutcome
from tests.presentator.adapters.support.build_support import (
    A_TOOLCHAIN_THAT_BUILDS,
    with_the_toolchain,
)
from tests.presentator.host.support.app_process import directory_name_of
from tests.presentator.host.support.app_processes import (
    KEY_DRAFT,
    App,
    Browser,
    serving_apps,
)
from tests.support.git_remote import EXAMPLE_SLUG, GitRemote, with_the_program
from tests.support.postgres import engine_on

_AT = datetime(2026, 11, 14, 12, tzinfo=UTC)
_SOURCES = "/api/sources"
_CHECK = "/api/sources/new/check"
_DECKS = "/api/decks"
_SETTINGS = "/api/settings"
_ACCOUNT = "/api/account"
_ROUNDS = RECENT_SOURCE_RUNS + 1
# The loser of two creations of one source is told the name or the address is
# taken, by the pre-check when the winner was already stored, else by the store.
_TAKEN = {SourceRefusal.DUPLICATE_NAME, SourceRefusal.DUPLICATE_URL}
_NOT_A_DRAFT = "not-a-draft"
_MARKING_ROUNDS = 12
_MARKED_DECKS = 12
# Stands in for the ssh git starts: it runs the upload-pack the remote command
# names on this machine, so an scp-form address reaches a repository on disk.
_SSH_ON_THIS_MACHINE = """#!/bin/sh
for command; do :; done
exec sh -c "git ${command#git-}"
"""
# Stands in for the ssh git starts, too, but reaches the repository its caller's
# route names: git tells ssh the known_hosts file of the app it runs for, and
# that file lies in the directory of that app alone.
_SSH_TO_THE_ROUTE_OF_THE_CALLER = """#!/bin/sh
for argument; do
    case "$argument" in UserKnownHostsFile=*) hosts=${{argument#*=}};; esac
done
caller=$(basename "$(dirname "$(dirname "$hosts")")")
exec git upload-pack "$(cat "{routes}/$caller")"
"""
_SSH_ADDRESS = "git@localhost:{repository}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    each = engine_on(database_url)
    try:
        yield each
    finally:
        each.dispose()


@pytest.fixture
def committed_remotes(
    remote: GitRemote, another_remote: GitRemote
) -> tuple[GitRemote, GitRemote]:
    """Two repositories that each carry a commit."""
    for each in (remote, another_remote):
        each.commit({"README.md": "talks"}, at=_AT)
    return remote, another_remote


@pytest.fixture
def remotes_over_ssh(
    environment: pytest.MonkeyPatch,
    tmp_path: Path,
    committed_remotes: tuple[GitRemote, GitRemote],
) -> tuple[GitRemote, GitRemote]:
    """Two repositories git reaches through an ssh that runs on this machine."""
    with_the_program(environment, tmp_path, "ssh", _SSH_ON_THIS_MACHINE)
    return committed_remotes


@pytest.fixture
def routes(environment: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The directory naming, per app, the repository its ssh reaches; builds succeed."""
    directory = tmp_path / "routes"
    directory.mkdir()
    with_the_program(
        environment,
        tmp_path,
        "ssh",
        _SSH_TO_THE_ROUTE_OF_THE_CALLER.format(routes=directory),
    )
    with_the_toolchain(environment, tmp_path, A_TOOLCHAIN_THAT_BUILDS)
    return directory


def _route(routes: Path, app: App, remote: GitRemote) -> None:
    """Let the ssh that `app` runs reach `remote`, whatever address it is given."""
    (routes / directory_name_of(app.port)).write_text(
        str(remote.bare), encoding="utf-8"
    )


def _scalar(engine: Engine, statement: str, **parameters: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(statement), parameters).scalar_one())


def _text(engine: Engine, statement: str) -> str:
    with engine.connect() as connection:
        return str(connection.execute(text(statement)).scalar_one())


def _texts(engine: Engine, statement: str) -> set[str]:
    with engine.connect() as connection:
        return {str(each) for each in connection.execute(text(statement)).scalars()}


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


def _opened_draft(admin: Browser, app: App) -> dict[str, str]:
    answer = admin.request(app, "POST", KEY_DRAFT)
    assert answer.status_code == HTTPStatus.OK, answer.text
    return answer.json()


def _listed_slugs(admin: Browser, app: App) -> list[str]:
    answer = admin.request(app, "GET", _DECKS)
    assert answer.status_code == HTTPStatus.OK
    return [deck["slug"] for deck in answer.json()["decks"]]


def _one_created_one_refused(
    answers: list[httpx2.Response], *, refused_as: set[SourceRefusal]
) -> None:
    assert sorted(each.status_code for each in answers) == [
        HTTPStatus.CREATED,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ], [each.text for each in answers]
    refused = [each.json()["reason"] for each in answers if each.is_client_error]
    assert len(refused) == 1
    assert refused[0] in refused_as


@pytest.mark.usefixtures("environment")
@pytest.mark.parametrize(
    "second",
    [("talks", 0), ("talks", 1), ("other-talks", 0)],
    ids=["one name and address", "one name", "one address"],
)
def test_a_source_created_at_both_processes_at_once_is_stored_once(
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    committed_remotes: tuple[GitRemote, GitRemote],
    second: tuple[str, int],
) -> None:
    second_name, second_remote = second
    with serving_apps(database_url, tmp_path) as apps:
        admin = _admin(apps[0])
        first = _checked(admin, apps[0], name="talks", url=committed_remotes[0].url)
        second_body = _checked(
            admin, apps[0], name=second_name, url=committed_remotes[second_remote].url
        )

        answers = _at_once(
            [
                lambda: _create(admin, apps[0], first),
                lambda: _create(admin, apps[1], second_body),
            ]
        )

    _one_created_one_refused(answers, refused_as=_TAKEN)
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
        draft = _opened_draft(admin, apps[0])
        bodies = [
            _checked(
                admin,
                apps[0],
                name=name,
                url=_SSH_ADDRESS.format(repository=each.bare),
                key_draft_id=draft["id"],
            )
            for name, each in zip(("first", "second"), remotes_over_ssh, strict=True)
        ]

        answers = _at_once(
            [
                lambda: _create(admin, apps[0], bodies[0]),
                lambda: _create(admin, apps[1], bodies[1]),
            ]
        )

    # A loser that reads the draft after the winner consumed it hears its key is
    # unavailable and is given a replacement, so only the original key is judged.
    _one_created_one_refused(
        answers, refused_as={*_TAKEN, SourceRefusal.DEPLOY_KEY_UNAVAILABLE}
    )
    key = draft["public_key"]
    assert _scalar(engine, "SELECT count(*) FROM sources") == 1
    assert (
        _scalar(
            engine, "SELECT count(*) FROM sources WHERE ssh_public_key = :key", key=key
        )
        == 1
    )
    assert (
        _scalar(
            engine,
            "SELECT count(*) FROM source_key_drafts WHERE public_key = :key",
            key=key,
        )
        == 0
    )


def _create_with_a_stale_draft(admin: Browser, app: App) -> httpx2.Response:
    """A creation whose draft is not the admin's current one, which replaces it."""
    return _create(
        admin,
        app,
        {
            "name": "talks",
            "url": _SSH_ADDRESS.format(repository="/nowhere.git"),
            "secret": None,
            "key_draft_id": _NOT_A_DRAFT,
            "fingerprint": "unchecked",
        },
    )


def _only_draft(engine: Engine) -> str:
    assert _scalar(engine, "SELECT count(*) FROM source_key_drafts") == 1
    return _text(engine, "SELECT id FROM source_key_drafts")


@pytest.mark.usefixtures("environment")
def test_a_key_draft_replaced_at_both_processes_at_once_leaves_one_new_draft(
    database_url: str, tmp_path: Path, engine: Engine
) -> None:
    with serving_apps(database_url, tmp_path) as apps:
        admin = _admin(apps[0])
        original = _opened_draft(admin, apps[0])["id"]

        answers = _at_once(
            [lambda app=app: _create_with_a_stale_draft(admin, app) for app in apps]
        )

        reopened = [_opened_draft(admin, app)["id"] for app in apps]

    assert [each.json()["reason"] for each in answers] == [
        SourceRefusal.DEPLOY_KEY_UNAVAILABLE
    ] * 2
    kept = _only_draft(engine)
    assert kept != original
    assert reopened == [kept, kept]


@pytest.mark.usefixtures("environment")
def test_a_key_draft_opened_beside_its_replacement_is_the_old_or_the_new_one(
    database_url: str, tmp_path: Path, engine: Engine
) -> None:
    with serving_apps(database_url, tmp_path) as apps:
        admin = _admin(apps[0])
        original = _opened_draft(admin, apps[0])["id"]

        opened, replaced = _at_once(
            [
                lambda: admin.request(apps[0], "POST", KEY_DRAFT),
                lambda: _create_with_a_stale_draft(admin, apps[1]),
            ]
        )

        reopened = [_opened_draft(admin, app)["id"] for app in apps]

    assert replaced.json()["reason"] == SourceRefusal.DEPLOY_KEY_UNAVAILABLE
    kept = _only_draft(engine)
    assert kept != original
    assert opened.json()["id"] in {original, kept}
    assert reopened == [kept, kept]


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
def test_fetches_of_trees_missing_each_others_decks_mark_what_one_fetch_would(
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    routes: Path,
    committed_remotes: tuple[GitRemote, GitRemote],
) -> None:
    first_tree, second_tree = committed_remotes
    decks_of = (
        [f"first-{number}" for number in range(_MARKED_DECKS)],
        [f"second-{number}" for number in range(_MARKED_DECKS)],
    )
    for tree, slugs in zip(committed_remotes, decks_of, strict=True):
        tree.commit_example_decks(at=_AT, into=slugs)
    with serving_apps(database_url, tmp_path) as apps:
        admin = _admin(apps[0])
        draft = _opened_draft(admin, apps[0])
        _route(routes, apps[0], first_tree)
        created = _create(
            admin,
            apps[0],
            _checked(
                admin,
                apps[0],
                name="talks",
                url=_SSH_ADDRESS.format(repository=first_tree.bare),
                key_draft_id=draft["id"],
            ),
        )
        assert created.status_code == HTTPStatus.CREATED, created.text
        _fetch(admin, apps[0], "talks")
        _route(routes, apps[0], second_tree)
        _fetch(admin, apps[0], "talks")
        _route(routes, apps[0], first_tree)
        _route(routes, apps[1], second_tree)

        outcomes: list[str] = []
        visible_after: list[set[str]] = []
        for _ in range(_MARKING_ROUNDS):
            answers = _at_once(
                [lambda app=app: _fetch(admin, app, "talks") for app in apps]
            )
            outcomes += [each.json()["run"]["outcome"] for each in answers]
            visible_after.append(
                _texts(engine, "SELECT slug FROM decks WHERE removed_at IS NULL")
            )

    assert outcomes == [SourceRunOutcome.SUCCESS] * (_MARKING_ROUNDS * len(apps))
    assert _scalar(engine, "SELECT count(*) FROM decks") == 2 * _MARKED_DECKS
    assert all(each in (set(decks_of[0]), set(decks_of[1])) for each in visible_after)


def test_a_deck_rebuilt_at_both_processes_at_once_is_delivered_from_one_build(
    environment: pytest.MonkeyPatch,
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    remote: GitRemote,
) -> None:
    with_the_toolchain(environment, tmp_path, A_TOOLCHAIN_THAT_BUILDS)
    remote.commit_example_deck(at=_AT)
    with serving_apps(database_url, tmp_path) as apps:
        admin = _admin(apps[0])
        _added(admin, apps[0], "talks", remote)
        remote.commit({f"{EXAMPLE_SLUG}/notes.md": "a new push"}, at=_AT)

        _at_once([lambda app=app: _fetch(admin, app, "talks") for app in apps])

    with engine.connect() as connection:
        talk, pdf, built_commit = connection.execute(
            text("SELECT active_build, pdf_export, built_commit FROM decks")
        ).one()
    assert built_commit == remote.head
    assert Path(talk).parent == Path(pdf).parent


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
