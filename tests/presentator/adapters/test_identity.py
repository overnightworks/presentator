"""The identity bridge against a migrated database of the test's own."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, text
from webauth.config import WebAuthConfig
from webauth.dependencies import ConnectionRefusal, admit_session
from webauth.ports import AccountKind

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.identity import StoredAccount, StoredSession, SystemClock
from presentator.adapters.postgres.sessions import PostgresSessionRecordStore
from presentator.adapters.postgres.sign_in import PostgresIdentityWrites
from presentator.application.identity import SESSION_LIFETIME
from presentator.contracts.models import Role, User
from presentator.host.main import web_auth_config_for
from tests.support.people import (
    a_session_id,
    seed_user,
)
from tests.support.redis import in_process_rate_limits

_NOW = datetime(2026, 1, 15, 9, tzinfo=UTC)
_ID_MARKER = "id-MARKER-4f7c1e"


@pytest.fixture
def database(engine: Engine) -> Engine:
    """This test's database, holding two admins."""
    seed_user(engine, an_admin())
    seed_user(engine, an_admin("user-2", "anna"))
    return engine


@pytest.fixture
def sessions(database: Engine) -> PostgresSessionRecordStore:
    return PostgresSessionRecordStore(database)


_TABLES = text(
    "SELECT table_name FROM information_schema.tables"
    " WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
    " ORDER BY table_name"
)
_COLUMNS = text(
    "SELECT column_name FROM information_schema.columns"
    " WHERE table_schema = current_schema() AND table_name = :table"
    " ORDER BY ordinal_position"
)


def _tables_of(connection: Connection) -> list[str]:
    return [name for (name,) in connection.execute(_TABLES)]


def _columns_of(connection: Connection, table: str) -> list[str]:
    return [name for (name,) in connection.execute(_COLUMNS, {"table": table})]


def _rows_of(connection: Connection, table: str) -> list[str]:
    """Every row of the table, each as Postgres writes it out."""
    return sorted(
        row
        for (row,) in connection.exec_driver_sql(
            f'SELECT CAST(t AS text) FROM "{table}" AS t'  # noqa: S608 - names from the catalogue
        )
    )


def an_id_token(word: str = _ID_MARKER) -> SecretStr:
    return SecretStr(word)


def open_session(  # noqa: PLR0913 - one keyword per stored column
    store: PostgresSessionRecordStore,
    user_id: str = "user-1",
    *,
    now: datetime = _NOW,
    expires_at: datetime = _NOW,
    refresh_token: SecretStr | None = None,
    account_language: str | None = None,
) -> StoredSession:
    return store.create(
        user_id,
        session_id=a_session_id(),
        now=now,
        expires_at=expires_at,
        refresh_token=refresh_token,
        id_token=an_id_token(),
        account_language=account_language,
    )


def an_admin(user_id: str = "user-1", username: str = "felix") -> User:
    return User(id=user_id, username=username, role=Role.ADMIN)


def the_account_of(person: User) -> StoredAccount:
    return StoredAccount(
        id=person.id,
        username=person.username,
        role=person.role,
        kind=AccountKind.PERSON,
    )


def stored_expiry(database: Engine, session_id: str) -> datetime:
    with database.connect() as connection:
        return connection.execute(
            text("SELECT expires_at FROM sessions WHERE id = :id"), {"id": session_id}
        ).scalar_one()


def session_count(database: Engine) -> int:
    with database.connect() as connection:
        return connection.exec_driver_sql("SELECT COUNT(*) FROM sessions").scalar_one()


def test_a_session_is_read_back_with_the_account_joined_from_the_users(
    sessions: PostgresSessionRecordStore,
) -> None:
    expires_at = _NOW + timedelta(hours=1)

    created = open_session(sessions, "user-1", expires_at=expires_at)

    loaded = sessions.load(created.id)
    assert loaded is not None
    assert loaded.user == the_account_of(an_admin())
    assert (loaded.created_at, loaded.expires_at) == (_NOW, expires_at)


def test_a_session_for_an_account_that_does_not_exist_is_refused_loudly(
    database: Engine,
    sessions: PostgresSessionRecordStore,
) -> None:
    with pytest.raises(LookupError, match="nobody"):
        open_session(sessions, "nobody")

    assert session_count(database) == 0


def test_a_touched_session_keeps_its_creation_and_takes_the_given_expiry(
    sessions: PostgresSessionRecordStore,
) -> None:
    session = open_session(sessions, "user-1")
    later = _NOW + timedelta(minutes=15)

    renewed = sessions.touch(session.id, expires_at=later)

    stored = sessions.load(session.id)
    assert renewed is True
    assert stored is not None
    assert (stored.created_at, stored.expires_at) == (_NOW, later)


def test_a_session_deleted_after_its_load_is_not_brought_back_by_touch(
    sessions: PostgresSessionRecordStore,
    database: Engine,
) -> None:
    logout = PostgresSessionRecordStore(database)
    session = open_session(sessions, "user-1")

    assert sessions.load(session.id) is not None
    logout.delete(session.id)
    renewed = sessions.touch(session.id, expires_at=_NOW + timedelta(minutes=1))

    assert renewed is False
    assert sessions.load(session.id) is None


def test_deleting_one_persons_sessions_counts_them_and_leaves_another_standing(
    sessions: PostgresSessionRecordStore,
) -> None:
    kept = open_session(sessions, "user-2")
    open_session(sessions, "user-1")

    dropped = sessions.delete_for_user("user-1")

    assert dropped == 1
    assert sessions.load(kept.id) is not None


def test_deleting_sessions_for_nobody_drops_nothing(
    sessions: PostgresSessionRecordStore,
) -> None:
    assert sessions.delete_for_user("nobody") == 0


def test_a_session_count_below_the_cap_drops_nothing(
    sessions: PostgresSessionRecordStore,
) -> None:
    created = [
        open_session(sessions, "user-1", now=_NOW + timedelta(minutes=minute))
        for minute in range(3)
    ]

    sessions.prune_overflow("user-1", keep_newest=5)

    assert all(sessions.load(session.id) is not None for session in created)


def test_a_timestamp_given_with_an_offset_is_stored_as_the_same_instant_in_utc(
    database: Engine,
    sessions: PostgresSessionRecordStore,
) -> None:
    in_berlin = timezone(timedelta(hours=1))

    session = sessions.create(
        "user-1",
        session_id=a_session_id(),
        now=_NOW.astimezone(in_berlin),
        expires_at=(_NOW + timedelta(hours=1)).astimezone(in_berlin),
        refresh_token=None,
        id_token=an_id_token(),
        account_language=None,
    )

    assert stored_expiry(database, session.id) == _NOW + timedelta(hours=1)
    loaded = sessions.load(session.id)
    assert loaded is not None
    assert loaded.created_at == _NOW


def test_the_system_clock_reads_an_aware_utc_time() -> None:
    assert SystemClock().now().tzinfo == UTC


def test_an_admitted_session_is_renewed_to_the_cap_when_the_window_reaches_it(
    database: Engine,
    sessions: PostgresSessionRecordStore,
) -> None:
    session = open_session(sessions, "user-1", expires_at=_NOW + timedelta(hours=1))
    at = _NOW + timedelta(minutes=30)

    admitted = admit_session(session.id, sessions=sessions, config=a_config_reading(at))

    assert not isinstance(admitted, ConnectionRefusal)
    assert admitted.username == "felix"
    assert stored_expiry(database, session.id) == _NOW + SESSION_LIFETIME


@pytest.mark.parametrize(
    ("idle_expiry", "refused_at"),
    [
        pytest.param(
            timedelta(hours=1), timedelta(hours=1, seconds=1), id="past-its-expiry"
        ),
        pytest.param(
            SESSION_LIFETIME - timedelta(minutes=1),
            SESSION_LIFETIME + timedelta(seconds=1),
            id="past-the-cap",
        ),
    ],
)
def test_a_refused_session_is_not_renewed(
    database: Engine,
    sessions: PostgresSessionRecordStore,
    idle_expiry: timedelta,
    refused_at: timedelta,
) -> None:
    session = open_session(sessions, "user-1", expires_at=_NOW + idle_expiry)
    expiry_before = stored_expiry(database, session.id)

    refused = admit_session(
        session.id, sessions=sessions, config=a_config_reading(_NOW + refused_at)
    )

    assert refused is ConnectionRefusal.SESSION_ENDED_OR_EXPIRED
    assert stored_expiry(database, session.id) == expiry_before


def a_config_reading(now: datetime) -> WebAuthConfig:
    return replace(
        web_auth_config_for(
            SecretStr("t" * 32),
            trusted_proxies="",
            origin="https://testserver",
            rate_limits=in_process_rate_limits(),
        ),
        wall_clock=now.timestamp,
    )


def test_a_fresh_database_stores_no_password_no_login_attempts_and_no_client_address(
    engine: Engine,
) -> None:
    """SI2, SI5: a fresh database stores no password, attempt or client address."""
    with engine.connect() as connection:
        user_columns = _columns_of(connection, "users")
        session_columns = _columns_of(connection, "sessions")
        tables = set(_tables_of(connection))

    assert user_columns == ["id", "username", "role", "kind"]
    assert session_columns == [
        "id",
        "user_id",
        "created_at",
        "expires_at",
        "refresh_token",
        "id_token",
        "account_language",
    ]
    assert "login_attempts" not in tables


@pytest.mark.parametrize(
    ("expires_at", "stays"),
    [
        (_NOW - timedelta(seconds=1), False),
        (_NOW, True),
        (_NOW + timedelta(seconds=1), True),
    ],
    ids=["before the sweep", "at the sweep", "after the sweep"],
)
def test_a_sweep_deletes_a_session_only_when_it_ended_before_the_sweep(
    sessions: PostgresSessionRecordStore,
    expires_at: datetime,
    *,
    stays: bool,
) -> None:
    """SI5: a sweep deletes a session only when it ended before the sweep."""
    session = open_session(
        sessions, "user-1", now=_NOW - SESSION_LIFETIME, expires_at=expires_at
    )

    deleted = sessions.delete_expired(_NOW)

    assert (sessions.load(session.id) is not None) is stays
    assert deleted == (0 if stays else 1)


@pytest.mark.parametrize(
    "after_expiry",
    [timedelta(0), timedelta(milliseconds=500), timedelta(seconds=1)],
    ids=["at the expiry", "within the second after", "a second after"],
)
def test_the_sweep_keeps_exactly_the_sessions_the_admission_admits(
    sessions: PostgresSessionRecordStore,
    after_expiry: timedelta,
) -> None:
    expires_at = _NOW + timedelta(hours=1)
    admitted_one = open_session(sessions, "user-1", expires_at=expires_at)
    swept_one = open_session(sessions, "user-2", expires_at=expires_at)
    at = expires_at + after_expiry

    admitted = admit_session(
        admitted_one.id, sessions=sessions, config=a_config_reading(at)
    )
    sessions.delete_expired(at)

    assert (not isinstance(admitted, ConnectionRefusal)) is (
        sessions.load(swept_one.id) is not None
    )


def dump_without_sessions(database: Engine) -> list[str]:
    with database.connect() as connection:
        return [
            f"{table}: {row}"
            for table in _tables_of(connection)
            if table != "sessions"
            for row in _rows_of(connection, table)
        ]


def test_a_sweep_leaves_the_accounts_and_every_other_table_as_they_were(
    database: Engine,
    sessions: PostgresSessionRecordStore,
) -> None:
    ended = _NOW - SESSION_LIFETIME
    open_session(sessions, "user-1", now=ended, expires_at=ended)
    before = dump_without_sessions(database)

    deleted = sessions.delete_expired(_NOW)

    assert deleted == 1
    assert any("felix" in statement for statement in before)
    assert dump_without_sessions(database) == before


def a_token(text: str = "rt-MARKER") -> SecretStr:
    return SecretStr(text)


def occurrences_in_the_database(database: Engine, word: str) -> int:
    """How often `word` appears in the rows of every table of the database."""
    with database.connect() as connection:
        return "\n".join(
            row
            for table in _tables_of(connection)
            for row in _rows_of(connection, table)
        ).count(word)


def test_a_refresh_token_is_stored_in_the_row_and_read_back_as_a_secret(
    database: Engine, sessions: PostgresSessionRecordStore
) -> None:
    """SI6: a refresh token is stored in the row and read back as a secret."""
    created = open_session(sessions, "user-1", refresh_token=a_token())

    loaded = sessions.load(created.id)

    assert loaded is not None
    assert loaded.refresh_token is not None
    assert loaded.refresh_token.get_secret_value() == "rt-MARKER"
    assert occurrences_in_the_database(database, "rt-MARKER") == 1


@pytest.mark.parametrize("language", ["de", "pt-BR", None])
def test_a_session_reads_back_the_account_language_it_was_created_with(
    sessions: PostgresSessionRecordStore, language: str | None
) -> None:
    created = open_session(sessions, "user-1", account_language=language)

    loaded = sessions.load(created.id)

    assert (created.account_language, loaded and loaded.account_language) == (
        language,
        language,
    )


def test_a_session_without_a_refresh_token_reads_back_none(
    sessions: PostgresSessionRecordStore,
) -> None:
    created = open_session(sessions, "user-1")

    loaded = sessions.load(created.id)

    assert loaded is not None
    assert loaded.refresh_token is None


def test_a_replaced_refresh_token_is_read_back_and_the_answer_is_true(
    sessions: PostgresSessionRecordStore,
) -> None:
    created = open_session(sessions, "user-1", refresh_token=a_token())

    other = open_session(sessions, "user-1", refresh_token=a_token("rt-other"))

    answer = sessions.replace_refresh_token(created.id, refresh_token=a_token("rt-2"))

    loaded = sessions.load(created.id)
    untouched = sessions.load(other.id)
    assert answer is True
    assert loaded is not None
    assert loaded.refresh_token == a_token("rt-2")
    assert untouched is not None
    assert untouched.refresh_token == a_token("rt-other")


def test_a_logout_on_another_store_before_a_replacement_leaves_the_row_gone(
    database: Engine, sessions: PostgresSessionRecordStore
) -> None:
    created = open_session(sessions, "user-1", refresh_token=a_token())
    PostgresSessionRecordStore(database).delete(created.id)

    answer = sessions.replace_refresh_token(created.id, refresh_token=a_token("rt-2"))

    assert answer is False
    assert sessions.load(created.id) is None
    assert occurrences_in_the_database(database, "rt-2") == 0


def test_replacing_the_token_of_a_never_created_session_creates_no_row(
    database: Engine, sessions: PostgresSessionRecordStore
) -> None:
    answer = sessions.replace_refresh_token("never-existed", refresh_token=a_token())

    assert answer is False
    assert session_count(database) == 0


def end_by_delete(store: PostgresSessionRecordStore, session_id: str) -> None:
    store.delete(session_id)


def end_by_delete_for_user(store: PostgresSessionRecordStore, session_id: str) -> None:
    del session_id
    store.delete_for_user("user-1")


def end_by_delete_expired(store: PostgresSessionRecordStore, session_id: str) -> None:
    del session_id
    store.delete_expired(_NOW + timedelta(days=1))


def end_by_prune_overflow(store: PostgresSessionRecordStore, session_id: str) -> None:
    del session_id
    store.prune_overflow("user-1", 0)


def end_by_delete_account(store: PostgresSessionRecordStore, session_id: str) -> None:
    del session_id
    PostgresIdentityWrites(store.engine, LoggingAuditSink()).delete_account("user-1")


@pytest.mark.parametrize(
    "remove",
    [
        end_by_delete,
        end_by_delete_for_user,
        end_by_delete_expired,
        end_by_prune_overflow,
        end_by_delete_account,
    ],
)
def test_every_way_of_ending_a_session_removes_its_token_with_the_row(
    database: Engine,
    sessions: PostgresSessionRecordStore,
    remove: Callable[[PostgresSessionRecordStore, str], None],
) -> None:
    created = open_session(sessions, "user-1", refresh_token=a_token())
    assert occurrences_in_the_database(database, "rt-MARKER") == 1
    assert occurrences_in_the_database(database, _ID_MARKER) == 1

    remove(sessions, created.id)

    assert occurrences_in_the_database(database, "rt-MARKER") == 0
    assert occurrences_in_the_database(database, _ID_MARKER) == 0
    with database.connect() as connection:
        owners = [
            table
            for table in _tables_of(connection)
            if "refresh_token" in _columns_of(connection, table)
        ]
    assert owners == ["sessions"]


def test_a_session_never_shows_its_tokens_in_repr_or_str(
    sessions: PostgresSessionRecordStore,
) -> None:
    """Neither `repr` nor `str` shows a token."""
    created = sessions.create(
        "user-1",
        session_id=a_session_id(),
        now=_NOW,
        expires_at=_NOW,
        refresh_token=a_token(),
        id_token=an_id_token("id-MARKER"),
        account_language=None,
    )

    for shown in (repr(created), str(created), repr(sessions.load(created.id))):
        assert "rt-MARKER" not in shown
        assert "id-MARKER" not in shown


def test_the_id_token_is_read_back_and_survives_a_touch_and_a_refresh_token_swap(
    sessions: PostgresSessionRecordStore,
) -> None:
    """The ID token reads back and outlives a touch and a swap."""
    created = sessions.create(
        "user-1",
        session_id=a_session_id(),
        now=_NOW,
        expires_at=_NOW,
        refresh_token=a_token(),
        id_token=an_id_token("id-kept"),
        account_language=None,
    )

    sessions.touch(created.id, expires_at=_NOW + timedelta(hours=1))
    sessions.replace_refresh_token(created.id, refresh_token=a_token("rt-2"))

    loaded = sessions.load(created.id)
    assert loaded is not None
    assert loaded.id_token is not None
    assert loaded.id_token.get_secret_value() == "id-kept"


def test_a_session_created_without_an_id_token_stores_none(
    database: Engine,
) -> None:
    """SI7: the caller states the ID token; NULL is stored for none."""
    store = PostgresSessionRecordStore(database)
    created = store.create(
        "user-1",
        session_id=a_session_id(),
        now=_NOW,
        expires_at=_NOW,
        refresh_token=None,
        id_token=None,
        account_language=None,
    )

    assert created.id_token is None
    loaded = store.load(created.id)
    assert loaded is not None
    assert loaded.id_token is None
