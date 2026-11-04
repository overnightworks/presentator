"""The groups reader: one refresh grant, its rotation, its refusals and cancellation."""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import pytest

from presentator.adapters.identity import SqliteSessionRecordStore
from presentator.agent.backend import SessionGroups
from presentator.contracts.copresenter import AccessRefusal, CoPresenterUnavailableError
from tests.presentator.agent.support.driving import on_an_event_loop
from tests.support.sign_in_support import Scene, a_scene

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from presentator.adapters.identity import StoredSession

REFRESH = "refresh-MARKER"
ROTATED = "rotated-MARKER"
THREAD_ERROR = "thread-error-MARKER"
GROUPS = ["/apps/presentator", "/other"]
LOGGER = "presentator.agent.backend"
_THREAD_WAIT_SECONDS = 10


class Arranged:
    """A signed-in session over a fake issuer, and the groups reader on it."""

    def __init__(self, scene: Scene) -> None:
        """Sign a person in; their one session row is the one asked about."""
        scene.issuer.issue_refresh_token(REFRESH)
        scene.sign_in(groups=GROUPS)
        (self.session_id,) = scene.stored_sessions()
        self.scene = scene
        self.reader = SessionGroups(scene.relying_party, scene.sessions, scene.config)


@pytest.fixture
def arranged(tmp_path: Path) -> Arranged:
    return Arranged(a_scene(tmp_path))


def test_a_signed_in_session_gets_the_groups_the_issuer_holds_now(
    arranged: Arranged,
) -> None:
    """C4: the groups of the refresh grant, not those of the sign-in."""
    arranged.scene.issuer.hold_groups(["/changed"])

    groups = asyncio.run(arranged.reader.groups_of(arranged.session_id))

    assert groups == frozenset({"/changed"})


def test_a_rotated_refresh_token_is_stored(arranged: Arranged) -> None:
    """C4: the library stores the rotation, and the reader asks the issuer once."""
    arranged.scene.issuer.rotate_refresh_token_to(ROTATED)
    before = len(arranged.scene.issuer.grant_types)

    asyncio.run(arranged.reader.groups_of(arranged.session_id))

    assert arranged.scene.stored_tokens() == [ROTATED]
    assert len(arranged.scene.issuer.grant_types) - before == 1


def test_a_revoked_refresh_token_asks_the_person_to_sign_in_again(
    arranged: Arranged,
) -> None:
    """C5."""
    arranged.scene.issuer.revoke_refresh_token(REFRESH)

    refusal = asyncio.run(arranged.reader.groups_of(arranged.session_id))

    assert refusal is AccessRefusal.SIGN_IN_AGAIN


def test_a_session_row_deleted_between_the_two_transactions_ends_the_session(
    arranged: Arranged,
) -> None:
    """C5: a row deleted mid-rotation ends the session."""
    arranged.scene.issuer.rotate_refresh_token_to(ROTATED)
    arranged.scene.issuer.during_token_request = lambda: arranged.scene.sessions.delete(
        arranged.session_id
    )

    refusal = asyncio.run(arranged.reader.groups_of(arranged.session_id))

    assert refusal is AccessRefusal.SESSION_ENDED
    assert arranged.scene.stored_tokens() == []


def test_an_issuer_that_is_down_is_one_warning_naming_the_reason(
    arranged: Arranged, caplog: pytest.LogCaptureFixture
) -> None:
    """C5: an unavailable issuer is one warning, with a closed reason."""
    arranged.scene.issuer.token_endpoint_is_down = True

    refusal = asyncio.run(arranged.reader.groups_of(arranged.session_id))

    assert refusal is AccessRefusal.ISSUER_UNAVAILABLE
    (warning,) = [
        record.getMessage() for record in caplog.records if record.name == LOGGER
    ]
    assert "token-endpoint-failed" in warning


class _LoadFails(SqliteSessionRecordStore):
    def load(self, session_id: str) -> StoredSession | None:
        error = RuntimeError(THREAD_ERROR)
        error.__context__ = RuntimeError(THREAD_ERROR)
        raise error


def test_a_thread_that_raises_is_one_unavailable_error_naming_only_its_type(
    arranged: Arranged, caplog: pytest.LogCaptureFixture
) -> None:
    """C5: the error is foreign and may hold a token, so only its type is shown."""
    scene = arranged.scene
    reader = SessionGroups(
        scene.relying_party, _LoadFails(scene.database), scene.config
    )
    caplog.set_level(logging.DEBUG)

    with pytest.raises(CoPresenterUnavailableError) as failed:
        asyncio.run(reader.groups_of(arranged.session_id))

    assert (failed.value.__cause__, failed.value.__context__) == (None, None)
    assert THREAD_ERROR not in caplog.text
    assert "reading the groups failed: RuntimeError" in caplog.text


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


def _hold_the_thread(arranged: Arranged) -> tuple[asyncio.Event, threading.Event]:
    """Park the grant's thread at the issuer; return (entered, release)."""
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()

    def hold_the_thread() -> None:
        loop.call_soon_threadsafe(entered.set)
        release.wait(_THREAD_WAIT_SECONDS)

    arranged.scene.issuer.during_token_request = hold_the_thread
    return entered, release


async def _cancel_while_the_thread_runs(
    arranged: Arranged, executor: RecordingExecutor
) -> asyncio.CancelledError:
    entered, release = _hold_the_thread(arranged)
    task = asyncio.create_task(arranged.reader.groups_of(arranged.session_id))
    await entered.wait()
    task.cancel()
    release.set()
    await asyncio.wrap_future(executor.recorded[0])
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    return caught.value


async def _cancel_as_the_thread_returns(
    arranged: Arranged, executor: RecordingExecutor
) -> asyncio.CancelledError:
    entered, release = _hold_the_thread(arranged)
    task = asyncio.create_task(arranged.reader.groups_of(arranged.session_id))
    await entered.wait()
    release.set()
    # Blocking the loop here keeps the returned thread's result undelivered, so
    # the cancellation reaches a task still waiting on a thread that is done.
    executor.recorded[0].result(_THREAD_WAIT_SECONDS)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    return caught.value


@pytest.mark.parametrize(
    "cancel",
    [_cancel_while_the_thread_runs, _cancel_as_the_thread_returns],
    ids=["inside-the-thread", "after-the-thread-returned"],
)
def test_a_cancelled_read_still_stores_the_rotation_and_leaves_no_chain(
    arranged: Arranged,
    cancel: Callable[[Arranged, RecordingExecutor], Awaitable[asyncio.CancelledError]],
) -> None:
    """C11: cancelling the groups read leaves no chain; the rotation stands."""
    arranged.scene.issuer.rotate_refresh_token_to(ROTATED)

    @on_an_event_loop
    async def drive() -> None:
        executor = RecordingExecutor()
        asyncio.get_running_loop().set_default_executor(executor)
        try:
            error = await cancel(arranged, executor)
        finally:
            executor.shutdown(wait=True)
        assert (error.__cause__, error.__context__) == (None, None)

    drive()

    assert arranged.scene.stored_tokens() == [ROTATED]


@pytest.mark.parametrize(
    "interruption", [KeyboardInterrupt(), SystemExit(1)], ids=["keyboard", "exit"]
)
def test_process_control_in_the_groups_thread_passes_through_without_a_chain(
    arranged: Arranged, interruption: BaseException
) -> None:
    """C11: an interrupt is the caller's, not one more unavailable error."""
    scene = arranged.scene

    class LoadInterrupts(SqliteSessionRecordStore):
        def load(self, session_id: str) -> StoredSession | None:
            raise interruption

    reader = SessionGroups(
        scene.relying_party, LoadInterrupts(scene.database), scene.config
    )

    with pytest.raises(type(interruption)) as passed:
        asyncio.run(reader.groups_of(arranged.session_id))

    assert passed.value is interruption
    assert (passed.value.__cause__, passed.value.__context__) == (None, None)
