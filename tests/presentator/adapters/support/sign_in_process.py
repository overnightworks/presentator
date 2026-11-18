"""One of two processes that sign the same subjects in against one Postgres database.

Run with `python -m` and the path of its events file. It says it is ready once
connected, then signs every subject in through the lobby's callback route when
the parent lets it go, writes the account events its sign-ins recorded to the
file, one `kind subject` line each, and exits 0 only when every sign-in went
through.
"""

import sys
from http import HTTPStatus
from pathlib import Path
from typing import Final

from presentator.api.auth import LOBBY_RATE_LIMIT
from tests.support.postgres import engine_from_environment
from tests.support.sign_in_support import a_scene
from tests.support.two_processes import announce_ready_and_wait_for_go

# Every sign-in of a process comes from one address, and each spends two requests
# of that address's sign-in budget: the start and the callback.
_SIGN_INS: Final = LOBBY_RATE_LIMIT.sign_in.max_requests // 2
SUBJECTS: Final = tuple(f"concurrent-subject-{number}" for number in range(_SIGN_INS))


def main() -> None:
    """Connect, wait for the go-ahead, sign every subject in, write the events."""
    (events_file,) = sys.argv[1:]
    engine = engine_from_environment()
    scene = a_scene(engine)
    announce_ready_and_wait_for_go()
    answers = [
        scene.sign_in(scene.browser(), subject=subject, username=subject)
        for subject in SUBJECTS
    ]
    engine.dispose()
    Path(events_file).write_text(
        "".join(
            f"{event.kind.value} {event.subject_id}\n" for event in scene.audit.events
        ),
        encoding="utf-8",
    )
    refused = [a.status_code for a in answers if a.status_code != HTTPStatus.SEE_OTHER]
    sys.exit(1 if refused else 0)


if __name__ == "__main__":
    main()
