"""The instance deletes expired sessions on a schedule of its own."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, text

from presentator.adapters.postgres.sessions import PostgresSessionRecordStore
from presentator.application.identity import SESSION_LIFETIME
from presentator.contracts.models import Role, User
from presentator.host import main
from presentator.host.config import load_settings
from tests.presentator.application.support.fakes import PATIENCE, FrozenClock
from tests.support.people import a_session_id, seed_user

_STARTED = datetime(2026, 1, 15, 9, tzinfo=UTC)
_ADMIN = User(id="id-of-felix", username="felix", role=Role.ADMIN)

pytestmark = pytest.mark.usefixtures("served_database")


@pytest.fixture
def clock(environment: pytest.MonkeyPatch) -> FrozenClock:
    clock = FrozenClock(_STARTED)
    environment.setattr(main, "SystemClock", lambda: clock)
    return clock


def stored_session_ids(database: Engine) -> set[str]:
    with database.connect() as connection:
        return set(connection.execute(text("SELECT id FROM sessions")).scalars())


def test_the_sweep_reads_the_clock_on_every_run_and_not_once_at_the_start(
    clock: FrozenClock, engine: Engine
) -> None:
    instance = main.build_instance(load_settings())
    database = engine
    seed_user(database, _ADMIN)
    session = PostgresSessionRecordStore(database).create(
        _ADMIN.id,
        session_id=a_session_id(),
        now=clock.now(),
        expires_at=clock.now() + SESSION_LIFETIME,
        refresh_token=None,
        id_token=SecretStr("an-id-token"),
        account_language=None,
    )

    asyncio.run(instance.session_sweep.tick())
    assert stored_session_ids(database) == {session.id}

    clock.advance(SESSION_LIFETIME + timedelta(seconds=1))
    asyncio.run(instance.session_sweep.tick())

    assert stored_session_ids(database) == set()
    with database.connect() as connection:
        assert list(
            connection.execute(text("SELECT username FROM users")).scalars()
        ) == ["felix"]


def test_the_sweep_runs_while_the_server_serves(
    clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
    engine: Engine,
) -> None:
    monkeypatch.setattr(main, "SESSION_SWEEP_INTERVAL", timedelta(0))
    database = engine
    deleted_a_session = threading.Event()
    delete_expired = PostgresSessionRecordStore.delete_expired

    def delete_expired_and_say_so(
        store: PostgresSessionRecordStore, at: datetime
    ) -> int:
        deleted = delete_expired(store, at)
        if deleted:
            deleted_a_session.set()
        return deleted

    monkeypatch.setattr(
        PostgresSessionRecordStore, "delete_expired", delete_expired_and_say_so
    )
    settings = load_settings()
    instance = main.build_instance(settings)

    with TestClient(main.served_by_host(instance, settings)):
        seed_user(database, _ADMIN)
        PostgresSessionRecordStore(database).create(
            _ADMIN.id,
            session_id=a_session_id(),
            now=clock.now() - SESSION_LIFETIME,
            expires_at=clock.now() - timedelta(seconds=1),
            refresh_token=None,
            id_token=SecretStr("an-id-token"),
            account_language=None,
        )
        assert deleted_a_session.wait(PATIENCE.total_seconds())

    assert stored_session_ids(database) == set()
