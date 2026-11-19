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

from presentator.adapters.postgres.locks import REFRESH_LOCK, LockClass, parameters
from presentator.contracts.decks import BuildFailureReason, BuildOutcome
from tests.presentator.host.support.app_processes import App, Browser, serving_apps
from tests.presentator.host.support.app_requests import (
    AT,
    SOURCES,
    added,
    admin_of,
    fetched,
    removal_of,
    scalar,
    watching,
)
from tests.presentator.host.support.build_gate import BuildGate, gated_toolchain
from tests.support.git_remote import EXAMPLE_SLUG, GitRemote
from tests.support.object_store import ObjectStore
from tests.support.postgres import (
    end_connections_holding,
    engine_on,
    granted_advisory_locks,
    waiting_advisory_locks,
)
from tests.support.sign_in_support import ADMIN

_WAIT_SECONDS = 60
_POLL_SECONDS = 0.05
# Neither process ticks inside a test that triggers its refreshes by request.
_NO_TICKS = {"PRESENTATOR_SOURCE_POLL_SECONDS": "3600"}
_TICKING = {"PRESENTATOR_SOURCE_POLL_SECONDS": "1"}
_ONE_SECOND = {"PRESENTATOR_BUILD_TIMEOUT_SECONDS": "1"}
_DECK_ROW = "SELECT built_commit, attempt_outcome FROM decks"
_BUILD_OF_THE_DECK = "SELECT built_run, built_commit FROM decks"
_BUILD_AND_TOOLCHAIN = "SELECT built_run, built_commit, built_toolchain FROM decks"
_HAL = "hal-client"
_TOKEN_CLIENTS = {"PRESENTATOR_TOKEN_CLIENTS": _HAL}
_PEOPLE = "/api/admin/people"
_WHAT_ERASURE_REMOVES = (
    "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM sources),"
    " (SELECT count(*) FROM decks)"
)
_TAKE_THE_REFRESH_LOCK = "SELECT pg_advisory_lock(:lock_class, :instance)"
_LET_GO_OF_THE_REFRESH_LOCK = "SELECT pg_advisory_unlock(:lock_class, :instance)"
_NEWEST_RUN = "SELECT coalesce(max(id), 0) FROM source_runs"
_ATTEMPT = "SELECT attempt_outcome FROM decks"
_ATTEMPT_STARTED = "SELECT attempt_outcome, attempt_started_at FROM decks"
# A trigger keeps each committed attempt write in order, so a short-lived one
# is seen without a reader racing the writer.
_RECORD_ATTEMPT_WRITES = """
CREATE TABLE attempt_writes (
    id bigserial PRIMARY KEY, outcome text, reason text
);
CREATE FUNCTION record_attempt_write() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO attempt_writes (outcome, reason)
    VALUES (NEW.attempt_outcome, NEW.attempt_reason);
    RETURN NEW;
END $$;
CREATE TRIGGER attempt_writes AFTER UPDATE OF attempt_outcome ON decks
FOR EACH ROW EXECUTE FUNCTION record_attempt_write();
"""
_ATTEMPT_WRITES = "SELECT outcome, reason FROM attempt_writes ORDER BY id"
_WAITING_TO_BUMP = (
    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
    " AND query LIKE 'UPDATE refresh_owner%' AND datname = current_database()"
)


def _stored(store: ObjectStore, settings: dict[str, str]) -> dict[str, str]:
    """The settings, with the one store both processes keep their builds in."""
    return {**settings, **store.environment}


@dataclass(frozen=True, slots=True)
class Database:
    """The test's own database, as the processes reach it and as the test reads it.

    `store` is where both processes keep the builds those rows name.
    """

    url: str
    engine: Engine
    store: ObjectStore


@pytest.fixture
def database(database_url: str, object_store: ObjectStore) -> Iterator[Database]:
    each = engine_on(database_url)
    try:
        yield Database(database_url, each, object_store)
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


def _record_attempt_writes(engine: Engine) -> None:
    """Keep every committed write of a deck's attempt, however soon the next follows."""
    with engine.begin() as connection:
        connection.exec_driver_sql(_RECORD_ATTEMPT_WRITES)


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
            database.url, tmp_path, _stored(database.store, _NO_TICKS)
        ) as apps,
        ThreadPoolExecutor(max_workers=2) as pool,
        watching(engine, _DECK_ROW) as seen,
    ):
        admin = admin_of(apps[0])
        _a_source_pushed_once_more(admin, apps[0], remote, gate)
        refreshing = pool.submit(fetched, admin, apps[0], "talks")
        _wait_until_building(engine)

        removing = pool.submit(removal_of, admin, apps[removing_at], "talks")
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
            database.url, tmp_path, _stored(database.store, _NO_TICKS)
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
            database.url, tmp_path, _stored(database.store, _NO_TICKS)
        ) as apps,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        _record_attempt_writes(engine)
        admin = admin_of(apps[0])
        _a_source_pushed_once_more(admin, apps[0], remote, gate)
        standing = _rows(engine, _BUILD_OF_THE_DECK)
        refreshing = pool.submit(fetched, admin, apps[0], "talks")
        _wait_until_building(engine)
        orphaned = _rows(engine, _ATTEMPT_STARTED)

        end_connections_holding(engine, LockClass.REFRESH)
        _eventually(
            (lambda: granted_advisory_locks(engine, LockClass.REFRESH)),
            lambda each: each == 0,
        )
        taking_over = pool.submit(fetched, admin, apps[1], "talks")
        _eventually(
            lambda: _rows(engine, _ATTEMPT_STARTED),
            lambda rows: rows != orphaned and rows[0][0] == BuildOutcome.RUNNING,
        )
        gate.let_through(2)
        taken_over = taking_over.result().json()
        runs_after_the_takeover = scalar(engine, _NEWEST_RUN)
        late = refreshing.result().json()
        rows = _rows(engine, _BUILD_OF_THE_DECK)
        stored = database.store.runs.names()
        runs_after_the_late_build = scalar(engine, _NEWEST_RUN)
        attempts = _rows(engine, _ATTEMPT_WRITES)

    assert taken_over["run"] is not None
    assert taken_over["decks_built"] == 1
    assert (BuildOutcome.FAILED, BuildFailureReason.INTERRUPTED) in attempts
    assert attempts[-1] == (None, None)
    assert late["run"] is None
    assert rows[0][1] == remote.head
    assert rows != standing
    assert stored is not None
    assert {run for run, _ in rows} <= stored
    assert runs_after_the_late_build == runs_after_the_takeover


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
        _stored(database.store, _NO_TICKS),
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
        runs_not_rebuilt = database.store.runs.names()

        apps[builder].process.kill()
        apps[builder].process.wait()
        _eventually(
            (lambda: granted_advisory_locks(engine, LockClass.TOOLCHAIN_PRESENCE)),
            lambda each: each == 1,
        )
        fetched(admin, apps[other], "talks")
        rebuilt = _rows(engine, _BUILD_AND_TOOLCHAIN)

    assert built[0][1] == remote.head
    assert not_rebuilt == built
    assert runs_not_rebuilt is not None
    assert len(runs_not_rebuilt) == 1
    assert rebuilt[0][1] == remote.head
    assert rebuilt[0][2] != built[0][2]


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
        database.url, tmp_path, _stored(database.store, _TICKING), second_environment
    ) as apps:
        added(admin_of(apps[0]), apps[0], "talks", remote)
        ticked = scalar(engine, _NEWEST_RUN)
        _eventually(_count(engine, _NEWEST_RUN), lambda newest: newest > ticked)

        end_connections_holding(engine, LockClass.REFRESH, LockClass.TOOLCHAIN_PRESENCE)
        after_the_end = scalar(engine, _NEWEST_RUN)

        _eventually(_count(engine, _NEWEST_RUN), lambda newest: newest > after_the_end)


def _fetch_status(admin: Browser, app: App, name: str) -> int:
    return admin.request(app, "POST", f"{SOURCES}/{name}/fetch").status_code


@pytest.mark.parametrize(
    "call",
    [(_fetch_status, HTTPStatus.OK), (removal_of, HTTPStatus.NO_CONTENT)],
    ids=["a fetch", "a removal"],
)
def test_a_call_right_after_the_lock_connection_ended_replaces_it_and_runs(
    database: Database,
    tmp_path: Path,
    *,
    remote_with_a_deck: GitRemote,
    gate: BuildGate,
    call: tuple[Callable[[Browser, App, str], int], HTTPStatus],
) -> None:
    ask, answered = call
    remote = remote_with_a_deck
    engine = database.engine
    gate.let_through(1)
    with serving_apps(
        database.url, tmp_path, _stored(database.store, _NO_TICKS)
    ) as apps:
        admin = admin_of(apps[0])
        added(admin, apps[0], "talks", remote)
        _eventually(
            (lambda: granted_advisory_locks(engine, LockClass.TOOLCHAIN_PRESENCE)),
            lambda each: each == len(apps),
        )
        end_connections_holding(engine, LockClass.TOOLCHAIN_PRESENCE)
        _eventually(
            (lambda: granted_advisory_locks(engine, LockClass.TOOLCHAIN_PRESENCE)),
            lambda each: each == 0,
        )

        status = ask(admin, apps[0], "talks")
        present_after = granted_advisory_locks(engine, LockClass.TOOLCHAIN_PRESENCE)

    assert status == answered
    assert present_after >= 1


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
        _stored(database.store, {**_TICKING, **_ONE_SECOND}),
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
            database.url, tmp_path, _stored(database.store, _NO_TICKS), _ONE_SECOND
        ) as apps,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        admin = admin_of(apps[0])
        _a_source_pushed_once_more(admin, apps[0], remote, gate)
        refreshing = pool.submit(fetched, admin, apps[0], "talks")
        _wait_until_building(engine)

        timed_out = removal_of(admin, apps[1], "talks")
        gate.let_through(1)
        refreshing.result()
        retried = removal_of(admin, apps[1], "talks")

    assert timed_out == HTTPStatus.INTERNAL_SERVER_ERROR
    assert retried == HTTPStatus.NO_CONTENT


def test_an_account_deletion_that_outwaits_its_bound_fails_and_a_retry_resumes_it(
    database: Database,
    tmp_path: Path,
    *,
    remote_with_a_deck: GitRemote,
    gate: BuildGate,
) -> None:
    remote = remote_with_a_deck
    engine = database.engine
    gate.let_through(1)
    with (
        serving_apps(
            database.url,
            tmp_path,
            _stored(database.store, {**_NO_TICKS, **_ONE_SECOND, **_TOKEN_CLIENTS}),
        ) as apps,
        engine.connect() as another_refresh,
    ):
        admin = admin_of(apps[0])
        added(admin, apps[0], "talks", remote)
        before = _rows(engine, _WHAT_ERASURE_REMOVES)
        # Each process has its own issuer keys, so the token is the erasing one's.
        token = apps[1].ask_access_token("operator", ADMIN, _HAL)
        erase = _erasure_of("admin", token)
        another_refresh.execute(text(_TAKE_THE_REFRESH_LOCK), parameters(REFRESH_LOCK))

        timed_out = erase(apps[1])
        left_after_the_timeout = _rows(engine, _WHAT_ERASURE_REMOVES)
        another_refresh.execute(
            text(_LET_GO_OF_THE_REFRESH_LOCK), parameters(REFRESH_LOCK)
        )
        retried = erase(apps[1])
        left_after_the_retry = _rows(engine, _WHAT_ERASURE_REMOVES)

    assert timed_out == HTTPStatus.INTERNAL_SERVER_ERROR
    assert left_after_the_timeout == before
    assert all(count != "0" for count in before[0])
    assert retried == HTTPStatus.OK
    assert left_after_the_retry == [("0", "0", "0")]


def _erasure_of(subject: str, token: str) -> Callable[[App], int]:
    def erase(app: App) -> int:
        return app.request(
            "DELETE",
            f"{_PEOPLE}/{subject}",
            {"authorization": f"Bearer {token}", "sec-fetch-site": "same-origin"},
        ).status_code

    return erase
