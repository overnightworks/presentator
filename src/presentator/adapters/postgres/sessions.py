"""The sessions of this product on Postgres, stored through webauth's extra."""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from pydantic import SecretStr
from sqlalchemy import Connection, Engine, func, select
from webauth.ports import SessionRecord
from webauth.postgres import (
    PostgresSessionStore,
    database_errors_as_store_errors,
    delete_expired,
)

from presentator.adapters.postgres.accounts import PostgresSubjectUserStore
from presentator.adapters.postgres.engine import in_utc
from presentator.adapters.postgres.session_schema import SESSIONS
from presentator.contracts.people import SessionExport, SessionSecretsHeld


@dataclass(frozen=True, slots=True)
class PostgresSessions:
    """The `sessions` table behind webauth's `SessionRecordStore` and the records.

    Every call is one transaction of its own, which the extra's store runs on.
    A database failure raises the extra's `SessionStoreError`. Instants are
    `timestamptz`, read and written as aware datetimes. The refresh token and
    the ID token sit in plain text in the session's row and leave with it; the
    row holds the SHA-256 digest of the session's id, never the id.
    """

    engine: Engine

    def create(  # noqa: PLR0913 - the webauth port fixes this signature
        self,
        user_id: str,
        *,
        session_id: str,
        now: datetime,
        expires_at: datetime,
        refresh_token: SecretStr | None,
        id_token: SecretStr | None,
        account_language: str | None,
    ) -> SessionRecord:
        """Open the session `session_id`, created at `now`, until `expires_at`."""
        with self._store() as store:
            return store.create(
                user_id,
                session_id=session_id,
                now=now,
                expires_at=expires_at,
                refresh_token=refresh_token,
                id_token=id_token,
                account_language=account_language,
            )

    def load(self, session_id: str) -> SessionRecord | None:
        """Read the row a cookie points at with its account; no account, no session."""
        with self._store() as store:
            return store.load(session_id)

    def touch(self, session_id: str, *, expires_at: datetime) -> bool:
        """Renew the row only while it still stands; a deleted session stays gone."""
        with self._store() as store:
            return store.touch(session_id, expires_at=expires_at)

    def replace_refresh_token(
        self,
        session_id: str,
        *,
        refresh_token: SecretStr,
    ) -> bool:
        """Replace the token only while the row stands; a deleted session stays gone."""
        with self._store() as store:
            return store.replace_refresh_token(session_id, refresh_token=refresh_token)

    def delete(self, session_id: str) -> None:
        """Delete the row, so logging out cannot be undone with the old cookie."""
        with self._store() as store:
            store.delete(session_id)

    def delete_for_user(self, user_id: str) -> int:
        """End every session of one account."""
        with self._store() as store:
            return store.delete_for_user(user_id)

    def prune_overflow(self, user_id: str, keep_newest: int) -> None:
        """End all but the `keep_newest` newest sessions by creation."""
        with self._store() as store:
            store.prune_overflow(user_id, keep_newest)

    def delete_expired(self, at: datetime) -> int:
        """Delete every session that ended before `at`, read in whole seconds."""
        with self._connection() as connection:
            return delete_expired(connection, SESSIONS, at)

    def sessions_of(self, user_id: str) -> tuple[SessionExport, ...]:
        """When each sign-in of one account began and ends and its language."""
        with self._connection() as connection:
            found = connection.execute(
                select(
                    SESSIONS.c.created_at,
                    SESSIONS.c.expires_at,
                    SESSIONS.c.account_language,
                ).where(SESSIONS.c.account_id == user_id)
            ).all()
        return tuple(
            SessionExport(
                created_at=in_utc(created_at),
                expires_at=in_utc(expires_at),
                account_language=account_language,
            )
            for created_at, expires_at, account_language in found
        )

    def secrets_held(self, user_id: str) -> SessionSecretsHeld:
        """How many sessions of one account hold each token, never the token."""
        with self._connection() as connection:
            refresh_tokens, id_tokens = connection.execute(
                select(
                    func.count(SESSIONS.c.refresh_token),
                    func.count(SESSIONS.c.id_token),
                ).where(SESSIONS.c.account_id == user_id)
            ).one()
        return SessionSecretsHeld(refresh_tokens=refresh_tokens, id_tokens=id_tokens)

    @contextmanager
    def _connection(self) -> Generator[Connection]:
        with database_errors_as_store_errors(), self.engine.begin() as connection:
            yield connection

    @contextmanager
    def _store(self) -> Generator[PostgresSessionStore]:
        with self._connection() as connection:
            yield PostgresSessionStore(
                connection,
                PostgresSubjectUserStore(self.engine, connection),
                SESSIONS,
            )
