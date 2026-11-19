"""The advisory locks this product takes on its database.

Every lock is the two-key form, so it can never collide with a lock a tool takes
by the single-key form. The first key is the lock's class, one id per kind of
lock in `LockClass`; the second is the instance within the class: 0 for a lock
there is one of, a hash for a lock per subject. Two classes never share a key
pair whatever a hash comes to, and each database is the product's alone, so no
product-wide key is needed.
"""

from dataclasses import dataclass
from enum import IntEnum
from hashlib import sha256
from typing import Final

from sqlalchemy import Connection, text

_ONLY_ONE = 0
_INSTANCE_BYTES: Final = 4
_TAKE_UNTIL_TRANSACTION_ENDS: Final = text(
    "SELECT pg_advisory_xact_lock(:lock_class, :instance)"
)


class LockClass(IntEnum):
    """One id per kind of lock, 32-bit."""

    MIGRATION = 1
    SIGN_IN = 2
    DEPLOY_KEY_DRAFT = 3
    REFRESH = 6
    TOOLCHAIN_PRESENCE = 7


@dataclass(frozen=True, slots=True)
class AdvisoryLock:
    """A lock `pg_advisory_lock(lock_class, instance)` takes, both keys 32-bit."""

    lock_class: LockClass
    instance: int


# Held by one `presentator migrate` at a time.
MIGRATION_LOCK: Final = AdvisoryLock(LockClass.MIGRATION, _ONLY_ONE)
# Held by one sign-in entry or account deletion at a time, across processes.
SIGN_IN_LOCK: Final = AdvisoryLock(LockClass.SIGN_IN, _ONLY_ONE)
# Held by the one process that takes sources in, builds and removes at a time.
REFRESH_LOCK: Final = AdvisoryLock(LockClass.REFRESH, _ONLY_ONE)


def lock_of(lock_class: LockClass, subject: str) -> AdvisoryLock:
    """The lock of one subject in `lock_class`.

    A hash collision only makes two subjects wait for each other.
    """
    digest = sha256(subject.encode()).digest()
    return AdvisoryLock(
        lock_class, int.from_bytes(digest[:_INSTANCE_BYTES], "big", signed=True)
    )


def parameters(lock: AdvisoryLock) -> dict[str, int]:
    """The two keys of `lock`, named as the lock statements take them."""
    return {"lock_class": int(lock.lock_class), "instance": lock.instance}


def hold_until_transaction_ends(connection: Connection, lock: AdvisoryLock) -> None:
    """Wait for `lock`, then hold it until the transaction commits or rolls back."""
    connection.execute(_TAKE_UNTIL_TRANSACTION_ENDS, parameters(lock))
