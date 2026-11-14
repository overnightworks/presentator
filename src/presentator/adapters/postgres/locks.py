"""The advisory locks this product takes on its database.

Every lock is the two-key form, so it can never collide with a lock a tool takes
by the single-key form. The first key names this product, the second the lock.
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class AdvisoryLock:
    """A lock `pg_advisory_lock(namespace, key)` takes, both keys 32-bit."""

    namespace: int
    key: int


PRODUCT_NAMESPACE: Final = 1_886_352_980
# Held by one `presentator migrate` at a time.
MIGRATION_LOCK: Final = AdvisoryLock(PRODUCT_NAMESPACE, 1)
