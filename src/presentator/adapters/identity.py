"""SQLite rows behind the identity ports.

The stores are this product's implementations of the `webauth` ports.
"""

import secrets
import sqlite3
import threading
from collections.abc import Generator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import SecretStr
from webauth.ports import AccountKind, AuditSink, SignInStores, UsernameTakenError

from presentator.adapters.audit import BufferedAudit
from presentator.adapters.sqlite import (
    apply_schema,
    immediate_transaction,
    rows_or_inside,
)
from presentator.contracts.models import Role, User
from presentator.contracts.people import SessionExport, SessionSecretsHeld

_KINDS: Final = ", ".join(f"'{kind.value}'" for kind in AccountKind)
KIND_COLUMN: Final = (
    f"kind TEXT NOT NULL DEFAULT '{AccountKind.PERSON.value}'"
    f" CHECK (kind IN ({_KINDS}))"
)
ACCOUNT_LANGUAGE_COLUMN: Final = "account_language TEXT"
_SCHEMA: Final = f"""
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,
    {KIND_COLUMN}
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    refresh_token TEXT,
    id_token TEXT,
    {ACCOUNT_LANGUAGE_COLUMN}
);
"""
_IDENTIFIER_BYTES: Final = 32
_USERNAME_COLUMN: Final = "users.username"
_SELECT_ACCOUNT: Final = "SELECT id, username, role, kind FROM users WHERE id = ?"


def create_identity_tables(database: Path) -> None:
    """Make the accounts and sessions tables exist."""
    apply_schema(database, _SCHEMA)


@dataclass(frozen=True, slots=True)
class SystemClock:
    """The wall clock, in UTC."""

    def now(self) -> datetime:
        """Read the current time."""
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ProcessWriteLock:
    """The one lock that serializes every identity write of this process.

    `SqliteIdentityWrites` holds it from the start of an entry until its audit
    events are flushed, so two entries never interleave their log lines. It
    defaults to a re-entrant `threading.RLock`, and is held only on worker
    threads, never on the event loop.
    """

    primitive: AbstractContextManager[object] = field(default_factory=threading.RLock)

    @contextmanager
    def hold(self) -> Generator[None]:
        """Serialize the check-then-write steps of this process."""
        with self.primitive:
            yield


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredAccount:
    """An account row as `webauth` reads it.

    The kind is `webauth`'s own `AccountKind`, which `contracts` may not import,
    so the row's record lives here and the lobby's `User` stays without it.
    """

    id: str
    username: str
    role: Role
    kind: AccountKind


@dataclass(frozen=True, slots=True)
class SqliteSubjectUserStore:
    """The accounts table, keyed by the identity provider's subject.

    Ids compare exactly: the column is binary, so a subject that differs only
    in case is another account. Every call commits on its own, except on a store
    that `SqliteIdentityWrites` bound to the cursor of its open transaction.
    """

    database: Path
    inside: sqlite3.Cursor | None = None

    def cursor(self) -> AbstractContextManager[sqlite3.Cursor]:
        """The open transaction's cursor, or a fresh one that commits on its way out."""
        return rows_or_inside(self.database, self.inside)

    def get(self, user_id: str) -> StoredAccount | None:
        """The account whose id is `user_id` character for character."""
        with self.cursor() as cursor:
            row = cursor.execute(_SELECT_ACCOUNT, (user_id,)).fetchone()
        return None if row is None else _account(row)

    def create_for_subject(
        self, subject: str, username: str, role: str, kind: AccountKind
    ) -> StoredAccount:
        """Add the account of `subject`; only a username another holds is 'taken'."""
        try:
            with self.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO users (id, username, role, kind) VALUES (?, ?, ?, ?)",
                    (subject, username, role, kind.value),
                )
        except sqlite3.IntegrityError as refused:
            if _is_a_taken_username(refused) and self.get(subject) is None:
                message = "the username is held by another account"
                raise UsernameTakenError(message) from None
            raise
        return StoredAccount(id=subject, username=username, role=Role(role), kind=kind)

    def set_role(self, user_id: str, role: str) -> StoredAccount:
        """Change the role of one existing account; no such account raises."""
        with self.cursor() as cursor:
            cursor.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
            if cursor.rowcount != 1:
                message = "no account to change the role of"
                raise LookupError(message)
            row = cursor.execute(_SELECT_ACCOUNT, (user_id,)).fetchone()
        return _account(row)

    def set_kind(self, user_id: str, kind: AccountKind) -> StoredAccount:
        """Change the kind of one existing account; no such account raises."""
        with self.cursor() as cursor:
            cursor.execute(
                "UPDATE users SET kind = ? WHERE id = ?", (kind.value, user_id)
            )
            if cursor.rowcount != 1:
                message = "no account to change the kind of"
                raise LookupError(message)
            row = cursor.execute(_SELECT_ACCOUNT, (user_id,)).fetchone()
        return _account(row)


def _is_a_taken_username(refused: sqlite3.IntegrityError) -> bool:
    return (
        refused.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_UNIQUE
        and _USERNAME_COLUMN in str(refused)
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredSession:
    """A stored sign-in: its account, its instants and its login's secrets.

    Whether it still stands is `webauth`'s `admit_session`, not a method here.
    """

    id: str
    user: StoredAccount
    created_at: datetime
    expires_at: datetime
    refresh_token: SecretStr | None
    id_token: SecretStr | None
    account_language: str | None


@dataclass(frozen=True, slots=True)
class SqliteSessionRecordStore:
    """The sessions table, joined to the accounts.

    Instants are stored as UTC ISO 8601 text, so comparing the text orders the
    instants. Every call commits on its own, except on a store that
    `SqliteIdentityWrites` bound to the cursor of its open transaction. The
    refresh token and the ID token sit in plain text in the session's row and
    leave with it.
    """

    database: Path
    inside: sqlite3.Cursor | None = None

    def cursor(self) -> AbstractContextManager[sqlite3.Cursor]:
        """The open transaction's cursor, or a fresh one that commits on its way out."""
        return rows_or_inside(self.database, self.inside)

    def create(  # noqa: PLR0913 - the webauth port fixes this signature
        self,
        user_id: str,
        *,
        now: datetime,
        expires_at: datetime,
        refresh_token: SecretStr | None,
        id_token: SecretStr | None,
        account_language: str | None,
    ) -> StoredSession:
        """Open a session created at `now` that stands until `expires_at`."""
        session_id = secrets.token_urlsafe(_IDENTIFIER_BYTES)
        with self.cursor() as cursor:
            account = cursor.execute(_SELECT_ACCOUNT, (user_id,)).fetchone()
            if account is None:
                message = f"no account {user_id} to open a session for"
                raise LookupError(message)
            cursor.execute(
                "INSERT INTO sessions"
                " (id, user_id, created_at, expires_at, refresh_token, id_token,"
                " account_language)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    user_id,
                    _stored(now),
                    _stored(expires_at),
                    None if refresh_token is None else refresh_token.get_secret_value(),
                    None if id_token is None else id_token.get_secret_value(),
                    account_language,
                ),
            )
        return StoredSession(
            id=session_id,
            user=_account(account),
            created_at=now,
            expires_at=expires_at,
            refresh_token=refresh_token,
            id_token=id_token,
            account_language=account_language,
        )

    def load(self, session_id: str) -> StoredSession | None:
        """Read the row a cookie points at with its account; no account, no session."""
        with self.cursor() as cursor:
            row = cursor.execute(
                "SELECT sessions.id, sessions.created_at, sessions.expires_at,"
                " sessions.refresh_token, sessions.id_token,"
                " sessions.account_language,"
                " users.id, users.username, users.role, users.kind"
                " FROM sessions JOIN users ON users.id = sessions.user_id"
                " WHERE sessions.id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        (
            stored_id,
            created_at,
            expires_at,
            refresh_token,
            id_token,
            account_language,
            *account,
        ) = row
        return StoredSession(
            id=stored_id,
            user=_account(account),
            created_at=datetime.fromisoformat(created_at),
            expires_at=datetime.fromisoformat(expires_at),
            refresh_token=None if refresh_token is None else SecretStr(refresh_token),
            id_token=None if id_token is None else SecretStr(id_token),
            account_language=account_language,
        )

    def touch(self, session_id: str, *, expires_at: datetime) -> bool:
        """Renew the row only while it still stands; a deleted session stays gone."""
        with self.cursor() as cursor:
            cursor.execute(
                "UPDATE sessions SET expires_at = ? WHERE id = ?",
                (_stored(expires_at), session_id),
            )
            return cursor.rowcount == 1

    def replace_refresh_token(
        self,
        session_id: str,
        *,
        refresh_token: SecretStr,
    ) -> bool:
        """Replace the token only while the row stands; a deleted session stays gone."""
        with self.cursor() as cursor:
            cursor.execute(
                "UPDATE sessions SET refresh_token = ? WHERE id = ?",
                (refresh_token.get_secret_value(), session_id),
            )
            return cursor.rowcount == 1

    def delete(self, session_id: str) -> None:
        """Delete the row, so logging out cannot be undone with the old cookie."""
        with self.cursor() as cursor:
            cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def delete_for_user(self, user_id: str) -> int:
        """End every session of one account."""
        with self.cursor() as cursor:
            cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            return int(cursor.rowcount)

    def sessions_of(self, user_id: str) -> tuple[SessionExport, ...]:
        """When each sign-in of one account began and ends and its language."""
        with self.cursor() as cursor:
            found = cursor.execute(
                "SELECT created_at, expires_at, account_language"
                " FROM sessions WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return tuple(
            SessionExport(
                created_at=datetime.fromisoformat(created_at),
                expires_at=datetime.fromisoformat(expires_at),
                account_language=account_language,
            )
            for created_at, expires_at, account_language in found
        )

    def secrets_held(self, user_id: str) -> SessionSecretsHeld:
        """How many sessions of one account hold each token, never the token."""
        with self.cursor() as cursor:
            refresh_tokens, id_tokens = cursor.execute(
                "SELECT COUNT(refresh_token), COUNT(id_token)"
                " FROM sessions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return SessionSecretsHeld(
            refresh_tokens=int(refresh_tokens), id_tokens=int(id_tokens)
        )

    def delete_expired(self, at: datetime) -> int:
        """Delete every session that ended before `at`; one ending at `at` stands.

        `at` is floored to the whole second, because admission decides by whole
        seconds: a session is still admitted anywhere in the second after its
        expiry, and a sweep must not delete what admission still admits.
        """
        cutoff = _stored(at.replace(microsecond=0))
        with self.cursor() as cursor:
            cursor.execute("DELETE FROM sessions WHERE expires_at < ?", (cutoff,))
            return int(cursor.rowcount)

    def prune_overflow(self, user_id: str, keep_newest: int) -> None:
        """End all but the `keep_newest` newest sessions by creation."""
        with self.cursor() as cursor:
            cursor.execute(
                "DELETE FROM sessions WHERE user_id = ? AND id NOT IN ("
                "SELECT id FROM sessions WHERE user_id = ?"
                " ORDER BY created_at DESC, id DESC LIMIT ?)",
                (user_id, user_id, keep_newest),
            )


@dataclass(frozen=True, slots=True)
class SqliteIdentityWrites:
    """Every identity write, each one atomic, serialized by the one `ProcessWriteLock`.

    Calling it enters webauth's `SignInTransaction`: one immediate transaction over
    accounts and sessions, rolled back when the block raises. The audit events of the
    entry reach the sink only after COMMIT, still under the lock, so the log names
    nothing that was rolled back. Deleting an account takes the same lock and
    transaction.
    """

    database: Path
    lock: ProcessWriteLock
    audit: AuditSink

    def __call__(self) -> AbstractContextManager[SignInStores]:
        """Enter one sign-in transaction; its stores share the entry's cursor."""
        return self._entry()

    @contextmanager
    def _entry(self) -> Generator[SignInStores]:
        buffered = BufferedAudit()
        with self.lock.hold():
            with immediate_transaction(self.database) as cursor:
                yield SignInStores(
                    accounts=SqliteSubjectUserStore(self.database, cursor),
                    sessions=SqliteSessionRecordStore(self.database, cursor),
                    audit=buffered,
                )
            buffered.flush_to(self.audit)

    def get(self, user_id: str) -> User | None:
        """The account of that id; a read needs no entry."""
        account = SqliteSubjectUserStore(self.database).get(user_id)
        if account is None:
            return None
        return User(id=account.id, username=account.username, role=account.role)

    def delete_account(self, user_id: str) -> None:
        """Delete the account and its sessions together."""
        with self.lock.hold(), immediate_transaction(self.database) as cursor:
            cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))


def _stored(instant: datetime) -> str:
    return instant.astimezone(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class TokenIdentifierFactory:
    """Ids nobody can guess."""

    def new_id(self) -> str:
        """Mint one url-safe token."""
        return secrets.token_urlsafe(_IDENTIFIER_BYTES)


def _account(row: Sequence[str]) -> StoredAccount:
    user_id, username, role, kind = row
    return StoredAccount(
        id=user_id, username=username, role=Role(role), kind=AccountKind(kind)
    )
