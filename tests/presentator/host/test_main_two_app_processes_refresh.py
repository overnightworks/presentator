"""Two app processes on one Postgres share one refresh owner, fenced and bounded.

Each case drives real processes through their requests and ticks and judges the
one database and the disk each process keeps.
"""

import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine, text

from presentator.adapters.postgres.locks import LockClass
from presentator.contracts.decks import BuildOutcome
from tests.presentator.host.support.app_processes import App, Browser, serving_apps
from tests.presentator.host.support.app_requests import (
    AT,
    SOURCES,
    added,
    admin_of,
    fetched,
    scalar,
    watching,
)
from tests.presentator.host.support.build_gate import BuildGate, gated_toolchain
from tests.support.git_remote import EXAMPLE_SLUG, GitRemote
from tests.support.postgres import (
    end_connections_holding,
    engine_on,
    granted_advisory_locks,
    waiting_advisory_locks,
)

_WAIT_SECONDS = 60
_POLL_SECONDS = 0.05
# Neither process ticks inside a test that triggers its refreshes by request.
_NO_TICKS = {"PRESENTATOR_SOURCE_POLL_SECONDS": "3600"}
_TICKING = {"PRESENTATOR_SOURCE_POLL_SECONDS": "1"}
_ONE_SECOND = {"PRESENTATOR_BUILD_TIMEOUT_SECONDS": "1"}
_DECK_ROW = "SELECT built_commit, attempt_outcome FROM decks"
_BUILD_OF_THE_DECK = "SELECT active_build, pdf_export, built_commit FROM decks"
_BUILD_AND_TOOLCHAIN = (
    "SELECT active_build, pdf_export, built_commit, built_toolchain FROM decks"
)
_NEWEST_RUN = "SELECT coalesce(max(id), 0) FROM source_runs"
_ATTEMPT = "SELECT attempt_outcome FROM decks"
_WAITING_TO_BUMP = (
    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
    " AND query LIKE 'UPDATE refresh_owner%' AND datname = current_database()"
)


def _shared_builds(tmp_path: Path, settings: dict[str, str]) -> dict[str, str]:
    """The settings, with one builds directory for both processes, as one disk."""
    return {**settings, "PRESENTATOR_BUILDS": str(tmp_path / "builds")}


@dataclass(frozen=True, slots=True)
class Database:
    """The test's own database, as the processes reach it and as the test reads it."""

    url: str
    engine: Engine


@pytest.fixture
def database(database_url: str) -> Iterator[Database]:
    each = engine_on(database_url)
    try:
        yield Database(database_url, each)
    finally:
        each.dispose()


@pytest.fixture
def remote_with_a_deck(remote: GitRemote) -> GitRemote:
    remote.commit_example_deck(at=AT)
    return remote


@pytest.fixture
def gate(environment: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[BuildGate]:
    """A toolchain whose builds wait, so a test decides when one ends."""
    with gated_toolchain(environment, tmp_path) as opened:
        yield opened


def _eventually[Seen](
    read: Callable[[], Seen], accepted: Callable[[Seen], bool]
) -> Seen:
    """What `read` answers once `accepted` takes it, within the wait."""
    deadline = time.monotonic() + _WAIT_SECONDS
    while True:
        seen = read()
        if accepted(seen):
            return seen
        assert time.monotonic() < deadline, f"never accepted, last seen {seen!r}"
        time.sleep(_POLL_SECONDS)


def _count(engine: Engine, statement: str) -> Callable[[], int]:
    return lambda: scalar(engine, statement)


def _rows(engine: Engine, statement: str) -> list[tuple[str | None, ...]]:
    with engine.connect() as connection:
        return [
            tuple(None if each is None else str(each) for each in row)
            for row in connection.execute(text(statement)).all()
        ]


def _push(remote: GitRemote, number: int) -> None:
    remote.commit({f"{EXAMPLE_SLUG}/notes.md": f"push {number}"}, at=AT)


def _wait_until_building(engine: Engine) -> None:
    _eventually(
        lambda: _rows(engine, _ATTEMPT), lambda rows: rows == [(BuildOutcome.RUNNING,)]
    )


def _a_source_pushed_once_more(
    admin: Browser, app: App, remote: GitRemote, gate: BuildGate
) -> None:
    """A source with a delivered talk, and one push that no build has met yet."""
    gate.let_through(1)
    added(admin, app, "talks", remote)
    _push(remote, 1)


def _removal(admin: Browser, app: App) -> int:
    """Confirm and request the removal of `talks`, answering its status code."""
    preview = admin.request(app, "GET", f"{SOURCES}/talks/removal").json()
    return admin.request(
        app,
        "DELETE",
        f"{SOURCES}/talks?deck_count={preview['deck_count']}"
        f"&run_count={preview['run_count']}",
    ).status_code


@pytest.mark.parametrize(
    "removing_at",
    [0, 1],
    ids=["the process that refreshes", "the other process"],
)
def test_a_removal_waits_for_the_refresh_of_either_process(
    database: Database,
    tmp_path: Path,
    *,
    remote_with_a_deck: GitRemote,
    gate: BuildGate,
    removing_at: int,
) -> None:
    remote = remote_with_a_deck
    engine = database.engine
    with (
        serving_apps(
            database.url, tmp_path, _shared_builds(tmp_path, _NO_TICKS)
        ) as apps,
        ThreadPoolExecutor(max_workers=2) as pool,
        watching(engine, _DECK_ROW) as seen,
    ):
        admin = admin_of(apps[0])
        _a_source_pushed_once_more(admin, apps[0], remote, gate)
        refreshing = pool.submit(fetched, admin, apps[0], "talks")
        _wait_until_building(engine)

        removing = pool.submit(_removal, admin, apps[removing_at])
        if removing_at != 0:
            # Another process waits on the database for the lock.
            _eventually(lambda: waiting_advisory_locks(engine), lambda each: each >= 1)
        gate.let_through(1)

        assert refreshing.result().json()["decks_built"] == 1
        assert removing.result() == HTTPStatus.NO_CONTENT

    delivered = (remote.head, None)
    assert delivered in [row for each in seen for row in each]
    assert seen[-1] == ()


def test_a_fetch_while_another_process_refreshes_does_nothing(
    database: Database,
    tmp_path: Path,
    *,
    remote_with_a_deck: GitRemote,
    gate: BuildGate,
) -> None:
    remote = remote_with_a_deck
    engine = database.engine
    with (
        serving_apps(
            database.url, tmp_path, _shared_builds(tmp_path, _NO_TICKS)
        ) as apps,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        admin = admin_of(apps[0])
        _a_source_pushed_once_more(admin, apps[0], remote, gate)
        refreshing = pool.submit(fetched, admin, apps[0], "talks")
        _wait_until_building(engine)

        turned_away = fetched(admin, apps[1], "talks").json()
        gate.let_through(1)
        refreshed = refreshing.result().json()

    assert turned_away["run"] is None
    assert refreshed["decks_built"] == 1


def test_a_process_that_lost_its_lock_connection_mid_build_is_replaced_and_refused(
    database: Database,
    tmp_path: Path,
    *,
    remote_with_a_deck: GitRemote,
    gate: BuildGate,
) -> None:
    remote = remote_with_a_deck
    engine = database.engine
    with (
        serving_apps(
            database.url, tmp_path, _shared_builds(tmp_path, _NO_TICKS)
        ) as apps,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        admin = admin_of(apps[0])
        _a_source_pushed_once_more(admin, apps[0], remote, gate)
        standing = _rows(engine, _BUILD_OF_THE_DECK)
        refreshing = pool.submit(fetched, admin, apps[0], "talks")
        _wait_until_building(engine)

        end_connections_holding(engine, LockClass.REFRESH)
        _eventually(
            (lambda: granted_advisory_locks(engine, LockClass.REFRESH)),
            lambda each: each == 0,
        )
        taken_over = fetched(admin, apps[1], "talks").json()
        attempt_taken_over = _rows(engine, _ATTEMPT)
        gate.let_through(1)
        late = refreshing.result().json()
        rows = _rows(engine, _BUILD_OF_THE_DECK)

    assert taken_over["run"] is not None
    assert attempt_taken_over == [(BuildOutcome.FAILED,)]
    assert late["run"] is None
    assert rows == standing
    assert all(Path(path).exists() for row in rows for path in row[:2] if path)


@pytest.mark.parametrize("builder", [0, 1], ids=["first builds", "second builds"])
def test_a_process_does_not_rebuild_what_a_present_toolchain_built_until_it_is_gone(
    database: Database,
    tmp_path: Path,
    *,
    remote_with_a_deck: GitRemote,
    gate: BuildGate,
    builder: int,
) -> None:
    another_toolchain = tmp_path / "another-toolchain"
    another_toolchain.mkdir()
    (another_toolchain / "package.json").write_text('{"name": "another"}')
    remote = remote_with_a_deck
    engine = database.engine
    gate.let_through(3)
    with serving_apps(
        database.url,
        tmp_path,
        _shared_builds(tmp_path, _NO_TICKS),
        {"PRESENTATOR_TOOLCHAIN": str(another_toolchain)},
    ) as apps:
        other = 1 - builder
        admin = admin_of(apps[builder])
        added(admin, apps[builder], "talks", remote)
        built = _rows(engine, _BUILD_AND_TOOLCHAIN)
        _eventually(
            (lambda: granted_advisory_locks(engine, LockClass.TOOLCHAIN_PRESENCE)),
            lambda each: each == len(apps),
        )

        fetched(admin, apps[other], "talks")
        not_rebuilt = _rows(engine, _BUILD_AND_TOOLCHAIN)
        runs_not_rebuilt = list((tmp_path / "builds").glob("*"))

        apps[builder].process.kill()
        apps[builder].process.wait()
        _eventually(
            (lambda: granted_advisory_locks(engine, LockClass.TOOLCHAIN_PRESENCE)),
            lambda each: each == 1,
        )
        fetched(admin, apps[other], "talks")
        rebuilt = _rows(engine, _BUILD_AND_TOOLCHAIN)

    assert built[0][2] == remote.head
    assert not_rebuilt == built
    assert len(runs_not_rebuilt) == 1
    assert rebuilt[0][2] == remote.head
    assert rebuilt[0][3] != built[0][3]


@pytest.mark.parametrize(
    "second_environment",
    [_TICKING, _NO_TICKS],
    ids=["both processes tick", "only the first ticks"],
)
def test_after_every_lock_connection_ends_a_process_refreshes_again(
    database: Database,
    tmp_path: Path,
    *,
    remote_with_a_deck: GitRemote,
    gate: BuildGate,
    second_environment: dict[str, str],
) -> None:
    remote = remote_with_a_deck
    engine = database.engine
    gate.let_through(1)
    with serving_apps(
        database.url, tmp_path, _shared_builds(tmp_path, _TICKING), second_environment
    ) as apps:
        added(admin_of(apps[0]), apps[0], "talks", remote)
        ticked = scalar(engine, _NEWEST_RUN)
        _eventually(_count(engine, _NEWEST_RUN), lambda newest: newest > ticked)

        end_connections_holding(engine, LockClass.REFRESH, LockClass.TOOLCHAIN_PRESENCE)
        after_the_end = scalar(engine, _NEWEST_RUN)

        _eventually(_count(engine, _NEWEST_RUN), lambda newest: newest > after_the_end)


def test_a_bump_blocked_by_a_stale_fenced_transaction_gives_up_and_succeeds_later(
    database: Database,
    tmp_path: Path,
    *,
    remote_with_a_deck: GitRemote,
    gate: BuildGate,
) -> None:
    remote = remote_with_a_deck
    engine = database.engine
    gate.let_through(1)
    with serving_apps(
        database.url,
        tmp_path,
        _shared_builds(tmp_path, {**_TICKING, **_ONE_SECOND}),
        _NO_TICKS,
    ) as apps:
        added(admin_of(apps[0]), apps[0], "talks", remote)
        with engine.connect() as stale:
            _hold_the_epoch_read_open(stale)

            _eventually(_count(engine, _WAITING_TO_BUMP), lambda each: each >= 1)
            runs_before = scalar(engine, _NEWEST_RUN)
            _eventually(
                (lambda: granted_advisory_locks(engine, LockClass.REFRESH)),
                lambda each: each == 0,
            )
            runs_while_blocked = scalar(engine, _NEWEST_RUN)

        _eventually(_count(engine, _NEWEST_RUN), lambda newest: newest > runs_before)

    assert runs_while_blocked == runs_before


def _hold_the_epoch_read_open(connection: Connection) -> None:
    connection.execute(text("SELECT epoch FROM refresh_owner FOR SHARE"))


def test_a_removal_that_outwaits_its_bound_fails_and_leaves_the_source(
    database: Database,
    tmp_path: Path,
    *,
    remote_with_a_deck: GitRemote,
    gate: BuildGate,
) -> None:
    remote = remote_with_a_deck
    engine = database.engine
    with (
        serving_apps(
            database.url, tmp_path, _shared_builds(tmp_path, _NO_TICKS), _ONE_SECOND
        ) as apps,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        admin = admin_of(apps[0])
        _a_source_pushed_once_more(admin, apps[0], remote, gate)
        refreshing = pool.submit(fetched, admin, apps[0], "talks")
        _wait_until_building(engine)

        timed_out = _removal(admin, apps[1])
        gate.let_through(1)
        refreshing.result()
        retried = _removal(admin, apps[1])

    assert timed_out == HTTPStatus.INTERNAL_SERVER_ERROR
    assert retried == HTTPStatus.NO_CONTENT
