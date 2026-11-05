"""A signed-in session over a fake issuer, and the readers that run on it."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from presentator.adapters.identity import SqliteSessionRecordStore
from presentator.adapters.session_access import SessionAccess
from tests.support.speech_service import A_SPEECH_SCOPE

if TYPE_CHECKING:
    from collections.abc import Callable

    from presentator.adapters.identity import StoredSession
    from tests.support.sign_in_support import Scene

REFRESH = "refresh-MARKER"
ROTATED = "rotated-MARKER"
THREAD_ERROR = "thread-error-MARKER"
GROUPS = ["/apps/presentator", "/other"]


class Arranged:
    """A signed-in session over a fake issuer, and the groups reader on it."""

    def __init__(self, scene: Scene) -> None:
        """Sign a person in; their one session row is the one asked about."""
        scene.issuer.issue_refresh_token(REFRESH)
        scene.sign_in(groups=GROUPS)
        (self.session_id,) = scene.stored_sessions()
        self.scene = scene
        self.reader = SessionAccess(
            scene.relying_party,
            scene.sessions,
            scene.config,
            speech_scope=A_SPEECH_SCOPE,
        )


class LoadFails(SqliteSessionRecordStore):
    """A session store whose read raises a foreign error holding a marker."""

    def load(self, session_id: str) -> StoredSession | None:
        error = RuntimeError(THREAD_ERROR)
        error.__context__ = RuntimeError(THREAD_ERROR)
        raise error


class RecordingExecutor(ThreadPoolExecutor):
    """A thread pool that keeps each future it hands out."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        super().__init__()
        self.recorded: list[Future[Any]] = []

    def submit[**Arguments, Result](
        self,
        fn: Callable[Arguments, Result],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Future[Result]:
        future = super().submit(fn, *args, **kwargs)
        self.recorded.append(future)
        return future
