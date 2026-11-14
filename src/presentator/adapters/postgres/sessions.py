"""The sessions table on Postgres, behind webauth's `SessionRecordStore`."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from pydantic import SecretStr
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from presentator.adapters.identity import StoredSession
from presentator.adapters.postgres.accounts import read_account, stored_account
from presentator.adapters.postgres.engine import connection_or_inside
from presentator.contracts.people import SessionExport, SessionSecretsHeld

_INSERT_SESSION: Final = text(
    "INSERT INTO sessions"
    " (id, user_id, created_at, expires_at, refresh_token, id_token, account_language)"
    " VALUES (:session_id, :user_id, :created_at, :expires_at, :refresh_token,"
    " :id_token, :account_language)"
)
_SELECT_SESSION: Final = text(
    "SELECT sessions.id, sessions.created_at, sessions.expires_at,"
    " sessions.refresh_token, sessions.id_token, sessions.account_language,"
    " users.id, users.username, users.role, users.kind"
    " FROM sessions JOIN users ON users.id = sessions.user_id"
    " WHERE sessions.id = :session_id"
)
_TOUCH: Final = text(
    "UPDATE sessions SET expires_at = :expires_at WHERE id = :session_id"
)
_REPLACE_REFRESH_TOKEN: Final = text(
    "UPDATE sessions SET refresh_token = :refresh_token WHERE id = :session_id"
)
_DELETE: Final = text("DELETE FROM sessions WHERE id = :session_id")
_DELETE_FOR_USER: Final = text("DELETE FROM sessions WHERE user_id = :user_id")
_SELECT_OF_USER: Final = text(
    "SELECT created_at, expires_at, account_language FROM sessions"
    " WHERE user_id = :user_id"
)
_COUNT_SECRETS: Final = text(
    "SELECT COUNT(refresh_token), COUNT(id_token) FROM sessions"
    " WHERE user_id = :user_id"
)
_DELETE_EXPIRED: Final = text("DELETE FROM sessions WHERE expires_at < :cutoff")
_PRUNE_OVERFLOW: Final = text(
    "DELETE FROM sessions WHERE user_id = :user_id AND id NOT IN ("
    "SELECT id FROM sessions WHERE user_id = :user_id"
    " ORDER BY created_at DESC, id DESC LIMIT :keep_newest)"
)


def _secret(token: str | None) -> SecretStr | None:
    return None if token is None else SecretStr(token)


def _revealed(token: SecretStr | None) -> str | None:
    return None if token is None else token.get_secret_value()


def _utc(instant: datetime) -> datetime:
    """The instant in UTC, whatever time zone the connection reads timestamps in."""
    return instant.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class PostgresSessionRecordStore:
    """The sessions table, joined to the accounts.

    Instants are `timestamptz`, read and written as aware datetimes. Every call
    commits on its own, except on a store that `PostgresIdentityWrites` bound to
    the connection of its open transaction. The refresh token and the ID token
    sit in plain text in the session's row and leave with it.
    """

    engine: Engine
    inside: Connection | None = None

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
    ) -> StoredSession:
        """Open the session `session_id`, created at `now`, standing until `expires_at`.

        Raises `ValueError` for an id the table holds, expired rows included, and
        leaves that row as it was.
        """
        with connection_or_inside(self.engine, self.inside) as connection:
            account = read_account(connection, user_id)
            if account is None:
                message = f"no account {user_id} to open a session for"
                raise LookupError(message)
            try:
                # A refused insert must not abort the entry's transaction.
                with connection.begin_nested():
                    connection.execute(
                        _INSERT_SESSION,
                        {
                            "session_id": session_id,
                            "user_id": user_id,
                            "created_at": now,
                            "expires_at": expires_at,
                            "refresh_token": _revealed(refresh_token),
                            "id_token": _revealed(id_token),
                            "account_language": account_language,
                        },
                    )
            except IntegrityError as refused:
                message = "a session with that id is already stored"
                raise ValueError(message) from refused
        return StoredSession(
            id=session_id,
            user=account,
            created_at=now,
            expires_at=expires_at,
            refresh_token=refresh_token,
            id_token=id_token,
            account_language=account_language,
        )

    def load(self, session_id: str) -> StoredSession | None:
        """Read the row a cookie points at with its account; no account, no session."""
        with connection_or_inside(self.engine, self.inside) as connection:
            row = connection.execute(
                _SELECT_SESSION, {"session_id": session_id}
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
            user=stored_account(account),
            created_at=_utc(created_at),
            expires_at=_utc(expires_at),
            refresh_token=_secret(refresh_token),
            id_token=_secret(id_token),
            account_language=account_language,
        )

    def touch(self, session_id: str, *, expires_at: datetime) -> bool:
        """Renew the row only while it still stands; a deleted session stays gone."""
        with connection_or_inside(self.engine, self.inside) as connection:
            renewed = connection.execute(
                _TOUCH, {"session_id": session_id, "expires_at": expires_at}
            )
        return renewed.rowcount == 1

    def replace_refresh_token(
        self,
        session_id: str,
        *,
        refresh_token: SecretStr,
    ) -> bool:
        """Replace the token only while the row stands; a deleted session stays gone."""
        with connection_or_inside(self.engine, self.inside) as connection:
            replaced = connection.execute(
                _REPLACE_REFRESH_TOKEN,
                {
                    "session_id": session_id,
                    "refresh_token": refresh_token.get_secret_value(),
                },
            )
        return replaced.rowcount == 1

    def delete(self, session_id: str) -> None:
        """Delete the row, so logging out cannot be undone with the old cookie."""
        with connection_or_inside(self.engine, self.inside) as connection:
            connection.execute(_DELETE, {"session_id": session_id})

    def delete_for_user(self, user_id: str) -> int:
        """End every session of one account."""
        with connection_or_inside(self.engine, self.inside) as connection:
            ended = connection.execute(_DELETE_FOR_USER, {"user_id": user_id})
        return ended.rowcount

    def sessions_of(self, user_id: str) -> tuple[SessionExport, ...]:
        """When each sign-in of one account began and ends and its language."""
        with connection_or_inside(self.engine, self.inside) as connection:
            found = connection.execute(_SELECT_OF_USER, {"user_id": user_id}).all()
        return tuple(
            SessionExport(
                created_at=_utc(created_at),
                expires_at=_utc(expires_at),
                account_language=account_language,
            )
            for created_at, expires_at, account_language in found
        )

    def secrets_held(self, user_id: str) -> SessionSecretsHeld:
        """How many sessions of one account hold each token, never the token."""
        with connection_or_inside(self.engine, self.inside) as connection:
            counts = connection.execute(_COUNT_SECRETS, {"user_id": user_id}).one()
        refresh_tokens, id_tokens = counts
        return SessionSecretsHeld(
            refresh_tokens=int(refresh_tokens), id_tokens=int(id_tokens)
        )

    def delete_expired(self, at: datetime) -> int:
        """Delete every session that ended before `at`; one ending at `at` stands.

        `at` is floored to the whole second, because admission decides by whole
        seconds: a session is still admitted anywhere in the second after its
        expiry, and a sweep must not delete what admission still admits.
        """
        with connection_or_inside(self.engine, self.inside) as connection:
            swept = connection.execute(
                _DELETE_EXPIRED, {"cutoff": at.replace(microsecond=0)}
            )
        return swept.rowcount

    def prune_overflow(self, user_id: str, keep_newest: int) -> None:
        """End all but the `keep_newest` newest sessions by creation."""
        with connection_or_inside(self.engine, self.inside) as connection:
            connection.execute(
                _PRUNE_OVERFLOW, {"user_id": user_id, "keep_newest": keep_newest}
            )
