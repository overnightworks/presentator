"""The identity records the Postgres stores share, and the clock and ids they use."""

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from pydantic import SecretStr
from webauth.ports import AccountKind

from presentator.contracts.models import Role, User

_IDENTIFIER_BYTES: Final = 32


@dataclass(frozen=True, slots=True)
class SystemClock:
    """The wall clock, in UTC."""

    def now(self) -> datetime:
        """Read the current time."""
        return datetime.now(UTC)


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


def lobby_user(account: StoredAccount | None) -> User | None:
    """The lobby's view of an account, which has no kind."""
    if account is None:
        return None
    return User(id=account.id, username=account.username, role=account.role)


@dataclass(frozen=True, slots=True)
class TokenIdentifierFactory:
    """Ids nobody can guess."""

    def new_id(self) -> str:
        """Mint one url-safe token."""
        return secrets.token_urlsafe(_IDENTIFIER_BYTES)


def stored_account(row: Sequence[str]) -> StoredAccount:
    """The record of one `users` row read as id, username, role, kind."""
    user_id, username, role, kind = row
    return StoredAccount(
        id=user_id, username=username, role=Role(role), kind=AccountKind(kind)
    )
