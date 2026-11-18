"""The source_key_drafts table on Postgres, behind `DeployKeyDrafts`."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Connection, Engine, text

from presentator.adapters.postgres.engine import in_utc
from presentator.adapters.postgres.locks import (
    LockClass,
    hold_until_transaction_ends,
    lock_of,
)
from presentator.adapters.secrets import DeployKeyPair, SecretBox, generate_deploy_key
from presentator.contracts.decks import DeployKeyDraft
from presentator.ports.identity import IdentifierFactory

_REPLACE_DRAFT: Final = text(
    "INSERT INTO source_key_drafts"
    " (id, owner_id, public_key, encrypted_private_key, created_at)"
    " VALUES (:draft_id, :owner_id, :public_key, :encrypted_private_key, :created_at)"
    " ON CONFLICT (owner_id) DO UPDATE SET"
    " id = EXCLUDED.id, public_key = EXCLUDED.public_key,"
    " encrypted_private_key = EXCLUDED.encrypted_private_key,"
    " created_at = EXCLUDED.created_at"
)
_DRAFT_OF_OWNER: Final = text(
    "SELECT id, owner_id, public_key, encrypted_private_key, created_at"
    " FROM source_key_drafts WHERE owner_id = :owner_id"
)
# The row lock keeps a second promotion of the same draft waiting until the
# first has deleted it, and then finding nothing.
_LOCK_OWNED_DRAFT: Final = text(
    "SELECT id, owner_id, public_key, encrypted_private_key, created_at"
    " FROM source_key_drafts"
    " WHERE id = :draft_id AND owner_id = :owner_id FOR UPDATE"
)
_TAKE_OWNED_DRAFT: Final = text(
    "DELETE FROM source_key_drafts WHERE id = :draft_id AND owner_id = :owner_id"
    " RETURNING id, owner_id, public_key, encrypted_private_key, created_at"
)
_REMOVE_DRAFT: Final = text("DELETE FROM source_key_drafts WHERE id = :draft_id")
_COUNT_FOR_OWNER: Final = text(
    "SELECT COUNT(*) FROM source_key_drafts WHERE owner_id = :owner_id"
)
_REMOVE_FOR_OWNER: Final = text(
    "DELETE FROM source_key_drafts WHERE owner_id = :owner_id"
)
_SWEEP: Final = text("DELETE FROM source_key_drafts WHERE created_at < :older_than")


@dataclass(frozen=True, slots=True, kw_only=True)
class PostgresSourceKeyDrafts:
    """The `source_key_drafts` table: at most one live keypair per admin.

    The owner column is unique, so replacing a draft in place is one atomic
    statement. Reusing a young draft and minting the next one both take the
    owner's own advisory lock first, so a mint never lands between another
    process's read of no draft and its write, and concurrent opens of Add in
    any number of processes agree on one draft.
    """

    engine: Engine
    identifiers: IdentifierFactory
    box: SecretBox
    new_key_pair: Callable[[], DeployKeyPair] = generate_deploy_key

    def unconsumed_for(
        self,
        owner_id: str,
        *,
        newer_than: datetime,
    ) -> DeployKeyDraft | None:
        """This admin's own unbound draft, or nothing while it has none this young."""
        with self.engine.connect() as connection:
            found = connection.execute(
                _DRAFT_OF_OWNER, {"owner_id": owner_id}
            ).fetchone()
        draft = None if found is None else _opened(found, self.box)
        return draft if draft is not None and draft.created_at >= newer_than else None

    def mint(self, owner_id: str, *, at: datetime) -> DeployKeyDraft:
        """Generate a fresh keypair and store it as this admin's own new draft."""
        with self.engine.begin() as connection:
            _hold_owner(connection, owner_id)
            return self._store_new(connection, owner_id, at=at)

    def get_or_mint(
        self,
        owner_id: str,
        *,
        newer_than: datetime,
        at: datetime,
    ) -> DeployKeyDraft:
        """Reuse one young draft or mint this owner's next one, one at a time."""
        with self.engine.begin() as connection:
            _hold_owner(connection, owner_id)
            found = connection.execute(
                _DRAFT_OF_OWNER, {"owner_id": owner_id}
            ).fetchone()
            draft = None if found is None else _opened(found, self.box)
            if draft is not None and draft.created_at >= newer_than:
                return draft
            return self._store_new(connection, owner_id, at=at)

    def bind(self, draft_id: str, *, owner_id: str) -> DeployKeyDraft | None:
        """Take that draft for a new source, only when this admin owns it."""
        with self.engine.begin() as connection:
            found = connection.execute(
                _TAKE_OWNED_DRAFT, {"draft_id": draft_id, "owner_id": owner_id}
            ).fetchone()
        return None if found is None else _opened(found, self.box)

    def count_for_owner(self, owner_id: str) -> int:
        """How many drafts this person holds, whether or not their key still opens."""
        with self.engine.connect() as connection:
            return connection.execute(
                _COUNT_FOR_OWNER, {"owner_id": owner_id}
            ).scalar_one()

    def remove_for_owner(self, owner_id: str) -> int:
        """Delete this person's drafts unread; how many rows went."""
        with self.engine.begin() as connection:
            return connection.execute(
                _REMOVE_FOR_OWNER, {"owner_id": owner_id}
            ).rowcount

    def sweep(self, *, older_than: datetime) -> None:
        """Delete every draft minted before that moment, private key and all."""
        with self.engine.begin() as connection:
            connection.execute(_SWEEP, {"older_than": older_than})

    def _store_new(
        self, connection: Connection, owner_id: str, *, at: datetime
    ) -> DeployKeyDraft:
        pair = self.new_key_pair()
        identifier = self.identifiers.new_id()
        connection.execute(
            _REPLACE_DRAFT,
            {
                "draft_id": identifier,
                "owner_id": owner_id,
                "public_key": pair.public_key,
                "encrypted_private_key": self.box.encrypt(pair.private_key),
                "created_at": at,
            },
        )
        return DeployKeyDraft(
            id=identifier,
            owner_id=owner_id,
            public_key=pair.public_key,
            private_key=pair.private_key,
            created_at=at,
        )


def _hold_owner(connection: Connection, owner_id: str) -> None:
    hold_until_transaction_ends(
        connection, lock_of(LockClass.DEPLOY_KEY_DRAFT, owner_id)
    )


def locked_owned_draft(
    connection: Connection, draft_id: str, *, owner_id: str, box: SecretBox
) -> DeployKeyDraft | None:
    """Lock that owner's draft inside the caller's transaction; nothing when none opens.

    The caller removes it with `remove_in` once its source row is written.
    """
    found = connection.execute(
        _LOCK_OWNED_DRAFT, {"draft_id": draft_id, "owner_id": owner_id}
    ).fetchone()
    return None if found is None else _opened(found, box)


def _opened(row: Sequence[Any], box: SecretBox) -> DeployKeyDraft | None:
    """The draft a row holds, or nothing when the instance key cannot open it."""
    identifier, owner_id, public_key, encrypted_private_key, created_at = row
    private_key = box.decrypt(encrypted_private_key)
    if private_key is None:
        return None
    return DeployKeyDraft(
        id=identifier,
        owner_id=owner_id,
        public_key=public_key,
        private_key=private_key,
        created_at=in_utc(created_at),
    )


def remove_in(connection: Connection, draft_id: str) -> None:
    """Delete one draft inside the caller's transaction."""
    connection.execute(_REMOVE_DRAFT, {"draft_id": draft_id})
