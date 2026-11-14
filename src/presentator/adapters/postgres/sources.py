"""The sources table on Postgres, behind `SourceStore`."""

from dataclasses import dataclass
from typing import Final

from psycopg.errors import UniqueViolation
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from presentator.adapters.decks import stored_source, written_source
from presentator.adapters.postgres.key_drafts import locked_owned_draft, remove_in
from presentator.adapters.secrets import SecretBox, generate_deploy_key
from presentator.contracts.decks import Source, SourceWrite
from presentator.ports.identity import IdentifierFactory

_ADD_SOURCE: Final = text(
    "INSERT INTO sources (id, name, url, ref, encrypted_secret, hook_secret_hash,"
    " owner_id, ssh_public_key, folder)"
    " VALUES (:source_id, :name, :url, :ref, :encrypted_secret, :hook_secret_hash,"
    " :owner_id, :public_key, :folder)"
)
_HOOK_HASH_BY_NAME: Final = text(
    "SELECT hook_secret_hash FROM sources WHERE name = :name"
)
_ALL_SOURCES: Final = text(
    "SELECT id, name, url, ref, owner_id, ssh_public_key, folder FROM sources"
)
_REMOVE_SOURCE: Final = text("DELETE FROM sources WHERE id = :source_id")
_PUT_ENCRYPTED_VALUE: Final = text(
    "UPDATE sources SET encrypted_secret = :encrypted_secret WHERE id = :source_id"
)
_PUT_DEPLOY_KEY: Final = text(
    "UPDATE sources SET encrypted_secret = :encrypted_secret,"
    " ssh_public_key = :public_key WHERE id = :source_id"
)
_PUT_HOOK_HASH: Final = text(
    "UPDATE sources SET hook_secret_hash = :digest WHERE name = :name"
)
_ONE_ROW: Final = 1


def _only_a_taken_name_or_url(refused: IntegrityError) -> None:
    """Pass a refused insert on to the caller's 'taken'; raise any other refusal."""
    if not isinstance(refused.orig, UniqueViolation):
        raise refused


@dataclass(frozen=True, slots=True, kw_only=True)
class PostgresSourceStore:
    """The sources table: one row per repository this instance mirrors.

    A name and a URL are each unique, so two processes adding the same one get
    one source and one refusal. Promoting a draft locks the draft's row first,
    so two processes promoting it get one source and one refusal.
    """

    engine: Engine
    identifiers: IdentifierFactory
    box: SecretBox

    def add(self, write: SourceWrite) -> Source | None:
        """Write a new source with its secrets, leaving every existing row alone.

        A name or URL this table already carries is refused with nothing rather
        than overwritten.
        """
        identifier = self.identifiers.new_id()
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    _ADD_SOURCE,
                    self._parameters(
                        identifier,
                        write,
                        secret=write.access_secret,
                        public_key=write.public_key,
                    ),
                )
        except IntegrityError as refused:
            _only_a_taken_name_or_url(refused)
            return None
        return written_source(identifier, write, public_key=write.public_key)

    def add_from_draft(self, write: SourceWrite, *, draft_id: str) -> Source | None:
        """Promote an owned draft only if its source row can be written too."""
        identifier = self.identifiers.new_id()
        try:
            with self.engine.begin() as connection:
                draft = locked_owned_draft(
                    connection, draft_id, owner_id=write.owner_id, box=self.box
                )
                if draft is None:
                    return None
                connection.execute(
                    _ADD_SOURCE,
                    self._parameters(
                        identifier,
                        write,
                        secret=draft.private_key,
                        public_key=draft.public_key,
                    ),
                )
                remove_in(connection, draft_id)
        except IntegrityError as refused:
            _only_a_taken_name_or_url(refused)
            return None
        return written_source(identifier, write, public_key=draft.public_key)

    def hook_secret_hash(self, name: str) -> bytes | None:
        """The stored hash of that source's webhook secret, if this name exists."""
        with self.engine.connect() as connection:
            found = connection.execute(_HOOK_HASH_BY_NAME, {"name": name}).fetchone()
        return None if found is None else found[0]

    def put_credential(self, source_id: str, secret: str) -> None:
        """Keep that source's read-only secret in its row, encrypted."""
        with self.engine.begin() as connection:
            connection.execute(
                _PUT_ENCRYPTED_VALUE,
                {"encrypted_secret": self.box.encrypt(secret), "source_id": source_id},
            )

    def renew_deploy_key(self, source_id: str) -> bool:
        """Replace one source's generated pair without touching its Add draft."""
        pair = generate_deploy_key()
        with self.engine.begin() as connection:
            written = connection.execute(
                _PUT_DEPLOY_KEY,
                {
                    "encrypted_secret": self.box.encrypt(pair.private_key),
                    "public_key": pair.public_key,
                    "source_id": source_id,
                },
            )
        return written.rowcount == _ONE_ROW

    def put_hook_secret_hash(self, name: str, digest: bytes) -> bool:
        """Replace that source's webhook-secret hash, or nothing when it is missing."""
        with self.engine.begin() as connection:
            written = connection.execute(
                _PUT_HOOK_HASH, {"digest": digest, "name": name}
            )
        return written.rowcount == _ONE_ROW

    def all(self) -> tuple[Source, ...]:
        """Read every source this instance mirrors decks from."""
        with self.engine.connect() as connection:
            found = connection.execute(_ALL_SOURCES).all()
        return tuple(stored_source(row) for row in found)

    def remove(self, source_id: str) -> None:
        """Delete that source's own row."""
        with self.engine.begin() as connection:
            connection.execute(_REMOVE_SOURCE, {"source_id": source_id})

    def _parameters(
        self,
        identifier: str,
        write: SourceWrite,
        *,
        secret: str,
        public_key: str | None,
    ) -> dict[str, str | bytes | None]:
        return {
            "source_id": identifier,
            "name": write.name,
            "url": write.url,
            "ref": write.ref,
            "encrypted_secret": self.box.encrypt(secret),
            "hook_secret_hash": write.hook_secret_hash,
            "owner_id": write.owner_id,
            "public_key": public_key,
            "folder": write.folder,
        }
