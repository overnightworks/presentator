"""One of two processes that ask the one Redis for the lease of a session.

Run with `python -m` in an environment that names the Redis and the lease
length. It answers each command line from its standard input with one line:
`admit` says `taken` or `busy`, `release` frees the lease it holds and says
`released`.
"""

import os
import sys
from typing import Final

from presentator.adapters.shared_redis import LeaseTiming, SharedRedis
from presentator.contracts.copresenter import RetryAfter
from presentator.ports.copresenter import AskTicket

SESSION: Final = "session-two-processes-ask"
TAKEN: Final = "taken"
BUSY: Final = "busy"
RELEASED: Final = "released"
URL_VARIABLE: Final = "ASK_LEASE_REDIS_URL"
LEASE_VARIABLE: Final = "ASK_LEASE_MILLISECONDS"


def run() -> None:
    """Answer commands until the parent closes the input."""
    lease = LeaseTiming(milliseconds=int(os.environ[LEASE_VARIABLE]))
    limiter = SharedRedis(os.environ[URL_VARIABLE], lease=lease).ask_limiter()
    held: AskTicket | None = None
    for command in sys.stdin:
        if command.strip() == "admit":
            admitted = limiter.admit(SESSION)
            if isinstance(admitted, RetryAfter):
                answer = BUSY
            else:
                held, answer = admitted, TAKEN
        else:
            if held is not None:
                held.release()
            answer = RELEASED
        print(answer, flush=True)  # noqa: T201 - the parent reads this line


if __name__ == "__main__":
    run()
