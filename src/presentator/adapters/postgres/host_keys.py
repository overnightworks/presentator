"""The host_key_pins table on Postgres, behind gitmirror's `HostKeyPins`."""

from dataclasses import dataclass
from typing import Final

from sqlalchemy import Engine, text

from gitmirror.model import HostKey

_PINNED: Final = text("SELECT host, public_key FROM host_key_pins ORDER BY host")
_PIN: Final = text(
    "INSERT INTO host_key_pins (host, public_key) VALUES (:host, :public_key)"
    " ON CONFLICT (host) DO NOTHING"
)
_PIN_OF_HOST: Final = text("SELECT public_key FROM host_key_pins WHERE host = :host")
_UNPIN: Final = text("DELETE FROM host_key_pins WHERE host = :host")


@dataclass(frozen=True, slots=True, kw_only=True)
class PostgresHostKeyPins:
    """The `host_key_pins` table: one key per host, and the first pin stands.

    The insert does nothing when the host has a key, and a conflicting insert
    waits for the other's commit, so the read after it sees the key that won.
    """

    engine: Engine

    def pinned(self) -> tuple[HostKey, ...]:
        """Every key pinned so far, one per host."""
        with self.engine.connect() as connection:
            rows = connection.execute(_PINNED).all()
        return tuple(HostKey(host=host, key=key) for host, key in rows)

    def pin(self, key: HostKey) -> HostKey:
        """Keep that key unless its host has one already; answer the key that stands."""
        with self.engine.begin() as connection:
            connection.execute(_PIN, {"host": key.host, "public_key": key.key})
            standing = connection.execute(_PIN_OF_HOST, {"host": key.host}).scalar_one()
        return HostKey(host=key.host, key=standing)

    def unpin(self, host: str) -> bool:
        """Remove that host's pin so the next run pins anew; false if it had none."""
        with self.engine.begin() as connection:
            removed = connection.execute(_UNPIN, {"host": host}).rowcount
        return removed > 0
