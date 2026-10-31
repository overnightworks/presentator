"""The instance deletes expired sessions on a schedule of its own."""

import asyncio
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import uvicorn
from pydantic import SecretStr

from presentator.adapters.identity import SqliteSessionRecordStore
from presentator.application.identity import SESSION_LIFETIME
from presentator.contracts.models import Role, User
from presentator.host import main
from presentator.host.config import load_settings
from tests.presentator.application.support.fakes import PATIENCE, FrozenClock
from tests.support.people import seed_user

_STARTED = datetime(2026, 1, 15, 9, tzinfo=UTC)
_ADMIN = User(id="id-of-felix", username="felix", role=Role.ADMIN)


@pytest.fixture
def clock(environment: pytest.MonkeyPatch) -> FrozenClock:
    clock = FrozenClock(_STARTED)
    environment.setattr(main, "SystemClock", lambda: clock)
    return clock


def stored_session_ids(database: Path) -> set[str]:
    with closing(sqlite3.connect(database)) as connection:
        return {row[0] for row in connection.execute("SELECT id FROM sessions")}


def test_the_sweep_reads_the_clock_on_every_run_and_not_once_at_the_start(
    clock: FrozenClock,
) -> None:
    instance = main.build_instance(load_settings())
    database = load_settings().database
    seed_user(database, _ADMIN)
    session = SqliteSessionRecordStore(database).create(
        _ADMIN.id,
        now=clock.now(),
        expires_at=clock.now() + SESSION_LIFETIME,
        refresh_token=None,
        id_token=SecretStr("an-id-token"),
    )

    asyncio.run(instance.session_sweep.tick())
    assert stored_session_ids(database) == {session.id}

    clock.advance(SESSION_LIFETIME + timedelta(seconds=1))
    asyncio.run(instance.session_sweep.tick())

    assert stored_session_ids(database) == set()
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT username FROM users").fetchall() == [
            ("felix",),
        ]


def test_the_sweep_runs_while_the_server_serves(
    clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "SESSION_SWEEP_INTERVAL", timedelta(0))
    database = load_settings().database
    swept: list[bool] = []
    deleted_a_session = threading.Event()
    delete_expired = SqliteSessionRecordStore.delete_expired

    def delete_expired_and_say_so(store: SqliteSessionRecordStore, at: datetime) -> int:
        deleted = delete_expired(store, at)
        if deleted:
            deleted_a_session.set()
        return deleted

    monkeypatch.setattr(
        SqliteSessionRecordStore, "delete_expired", delete_expired_and_say_so
    )

    class ServerThatWaitsForTheSweep:
        def __init__(self, config: uvicorn.Config) -> None:
            self.config = config

        async def serve(self) -> None:
            seed_user(database, _ADMIN)
            SqliteSessionRecordStore(database).create(
                _ADMIN.id,
                now=clock.now() - SESSION_LIFETIME,
                expires_at=clock.now() - timedelta(seconds=1),
                refresh_token=None,
                id_token=SecretStr("an-id-token"),
            )
            await asyncio.to_thread(deleted_a_session.wait, PATIENCE.total_seconds())
            swept.append(not stored_session_ids(database))

    monkeypatch.setattr(main.uvicorn, "Server", ServerThatWaitsForTheSweep)

    main.main()

    assert swept == [True]
