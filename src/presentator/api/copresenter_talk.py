"""The co-presenter's talk socket: the model hears the whole talk and may answer.

The socket carries the person's microphone up with the browser's own messages and
the model's sound down. Every frame down goes through one queue, so frames never
interleave and the model's loop can send one without waiting.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from base64 import b64encode
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Final, Literal, assert_never, cast

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from logline import StepTimer
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool

from presentator.api.copresenter import (
    GENERIC_ANSWER_FAILURE,
    INTERNAL_ERROR,
    POLICY_VIOLATION,
    SERVICE_RESTART,
    AnswerFailure,
    AnswerRefusal,
    SessionGuard,
    finish_tasks,
    observe_session_end,
    read_tokens,
    watch_session,
)
from presentator.api.session_api import language_of
from presentator.contracts.copresenter import (
    AnswerStopReason,
    DecisionBegan,
    DecisionEnded,
    DecisionStopped,
    EchoDropped,
    HeardInTalk,
    HearingTranscript,
    ShownSlide,
    SoundRefusedError,
    TalkEvent,
    UnreadableTalkMessageError,
)
from presentator.contracts.words import Language

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from webauth.ports import SessionRecordStore

    from presentator.api.copresenter import Admission, CoPresenterSurface, SignedIn
    from presentator.contracts.copresenter import AskedDeck, Audio, SpeechTokenSource

_log = logging.getLogger(__name__)
_TALK_PATH: Final = "/copresenter/talk"
_QUERY_KEYS: Final = frozenset({"deck", "language"})


class SoundEnd(StrEnum):
    """How the browser reports a sound it was sent."""

    ENDED = "ended"
    REFUSED = "refused"
    FAILED = "failed"


class PlayedFrame(BaseModel, extra="forbid", strict=True):
    """The browser says how a sound it was sent ended."""

    kind: Literal["played"]
    id: int
    end: SoundEnd


class ShownFrame(BaseModel, extra="forbid", strict=True):
    """The browser says which slide its deck shows."""

    kind: Literal["shown"]
    slide: int = Field(ge=1)


_UpFrame: Final[TypeAdapter[PlayedFrame | ShownFrame]] = TypeAdapter(
    Annotated[PlayedFrame | ShownFrame, Field(discriminator="kind")],
    config={"strict": True},
)


def read_up_frame(text: str) -> PlayedFrame | ShownFrame:
    """The browser's message as a frame; anything else is an unreadable message."""
    try:
        return _UpFrame.validate_json(text)
    except ValidationError:
        raise UnreadableTalkMessageError from None


class _DownFrames:
    """Every frame to the browser, in the order it was queued; one task sends them."""

    def __init__(self, socket: WebSocket) -> None:
        self._socket = socket
        self._queue = asyncio.Queue[dict[str, object]]()

    def send(self, frame: dict[str, object]) -> None:
        self._queue.put_nowait(frame)

    async def drained(self) -> None:
        await self._queue.join()

    async def send_all(self) -> None:
        """Send each queued frame; a socket that is gone drops the rest."""
        gone = False
        while True:
            frame = await self._queue.get()
            if not gone:
                try:
                    await self._socket.send_json(frame)
                except WebSocketDisconnect, RuntimeError:
                    gone = True
            self._queue.task_done()


def _error_frame(
    reason: AnswerStopReason | None = None, *, retry_after_s: int | None = None
) -> dict[str, object]:
    frame: dict[str, object] = {"kind": "error", "message": GENERIC_ANSWER_FAILURE}
    if reason is not None:
        frame["reason"] = reason.value
    if retry_after_s is not None:
        frame["retry_after_s"] = retry_after_s
    return frame


class _TalkHearing:
    """What the talk hears: finished lines in order, and the slide the deck shows now.

    A slide the browser shows overwrites the one not yet read, so a browser that
    shows slides without pause never grows it.
    """

    def __init__(self) -> None:
        self._finals = deque[HearingTranscript]()
        self._slide: int | None = None
        self._ended = False
        self._moved = asyncio.Event()

    def hear(self, transcript: HearingTranscript) -> None:
        self._finals.append(transcript)
        self._moved.set()

    def show(self, number: int) -> None:
        self._slide = number
        self._moved.set()

    def end(self) -> None:
        self._ended = True
        self._moved.set()

    def __aiter__(self) -> _TalkHearing:
        return self

    async def __anext__(self) -> HeardInTalk:
        while True:
            if self._slide is not None:
                number, self._slide = self._slide, None
                return ShownSlide(number=number)
            if self._finals:
                return self._finals.popleft()
            if self._ended:
                raise StopAsyncIteration
            self._moved.clear()
            await self._moved.wait()


class _Cut(StrEnum):
    """How the end of the talk cut a decision still open."""

    CANCELLED = "cancelled"
    SESSION_ENDED = "session_ended"
    UNEXPECTED = "unexpected"


@dataclass(slots=True)
class _OpenDecision:
    step: StepTimer
    trace: contextvars.Context


class _Decisions:
    """The `answer.ended` line of each decision, in its waking line's trace."""

    def __init__(self, down: _DownFrames) -> None:
        self._down = down
        self._open: _OpenDecision | None = None

    def __call__(self, event: TalkEvent) -> None:
        match event:
            case DecisionBegan():
                self._open = _OpenDecision(
                    StepTimer("answer.ended", _log), contextvars.copy_context()
                )
            case DecisionStopped():
                self._stopped(event)
            case DecisionEnded():
                self._decision().step.end()
                self._open = None
            case EchoDropped():
                _log.info("echo.dropped")
            case _:
                assert_never(event)

    def first_output(self) -> None:
        """Mark the first sound of the decision that is open."""
        self._decision().step.first_output()

    def cut(self, how: _Cut) -> None:
        """End the decision the talk's end cut, in the trace it began in."""
        if self._open is None:
            return
        decision, self._open = self._open, None
        decision.trace.run(_end_cut, decision.step, how)

    def _decision(self) -> _OpenDecision:
        # The loop reports a decision's start before anything else of it.
        return cast("_OpenDecision", self._open)

    def _stopped(self, event: DecisionStopped) -> None:
        step = self._decision().step
        if event.reason is None:
            step.fail(AnswerFailure.UNAVAILABLE)
            step.handled()
        else:
            step.refuse(event.reason.name.lower())
        self._down.send(_error_frame(event.reason, retry_after_s=event.retry_after_s))


def _end_cut(step: StepTimer, how: _Cut) -> None:
    match how:
        case _Cut.CANCELLED:
            step.end_cancelled()
        case _Cut.SESSION_ENDED:
            step.refuse(AnswerRefusal.SESSION_ENDED)
            step.end()
        case _Cut.UNEXPECTED:
            step.fail(AnswerFailure.UNEXPECTED)
            step.end()
        case _:
            assert_never(how)


class _TalkBrowser:
    """The sounds sent down and the slide moves; the browser's reports end a play."""

    def __init__(self, down: _DownFrames, first_output: Callable[[], None]) -> None:
        self._down = down
        self._first_output = first_output
        self._last_id = 0
        self._playing: tuple[int, asyncio.Future[SoundEnd]] | None = None

    async def play(self, audio: Audio) -> None:
        self._last_id += 1
        sound = self._last_id
        ended = asyncio.get_running_loop().create_future()
        self._playing = (sound, ended)
        self._first_output()
        self._down.send(
            {
                "kind": "audio",
                "id": sound,
                "text": audio.text,
                "wav_b64": b64encode(audio.wav).decode("ascii"),
                "ai_generated": True,
            }
        )
        try:
            end = await ended
        except asyncio.CancelledError:
            self._down.send({"kind": "stop", "id": sound})
            raise
        finally:
            self._playing = None
        if end is not SoundEnd.ENDED:
            raise SoundRefusedError

    def show_slide(self, number: int) -> None:
        self._down.send({"kind": "slide", "number": number})

    def played(self, sound: int, end: SoundEnd) -> None:
        """The browser reports a sound; one no play waits on is ignored."""
        if self._playing is None:
            return
        waiting, ended = self._playing
        if waiting == sound and not ended.done():
            ended.set_result(end)


def _handler_of(browser: _TalkBrowser, hearing: _TalkHearing) -> Callable[[str], None]:
    def handle(text: str) -> None:
        frame = read_up_frame(text)
        match frame:
            case PlayedFrame():
                browser.played(frame.id, frame.end)
            case ShownFrame():
                hearing.show(frame.slide)
            case _:
                assert_never(frame)

    return handle


def add_copresenter_talk_route(
    app: FastAPI,
    *,
    surface: CoPresenterSurface,
    admission: Admission,
    sessions: SessionRecordStore,
) -> None:
    """Add the talk socket, which the model answers on, to the talk host."""
    guard = SessionGuard(admission=admission, delay=surface.delay)
    app.add_api_websocket_route(
        _TALK_PATH, _talk_route(surface, admission, guard, sessions)
    )


def _talk_route(
    surface: CoPresenterSurface,
    admission: Admission,
    guard: SessionGuard,
    sessions: SessionRecordStore,
) -> Callable[[WebSocket, str, str], Awaitable[None]]:
    async def _talk(socket: WebSocket, deck: str, language: str) -> None:
        if (
            socket.headers.get("origin") != surface.browser_origin
            or not _QUERY_KEYS.issuperset(socket.query_params)
            or any(len(socket.query_params.getlist(key)) > 1 for key in _QUERY_KEYS)
        ):
            await socket.close(code=POLICY_VIOLATION)
            return
        connection = admission(socket)
        if connection is None:
            await socket.close(code=POLICY_VIOLATION)
            return
        await socket.accept()
        if surface.stopping():
            await socket.close(code=SERVICE_RESTART)
            return
        asked = await run_in_threadpool(
            surface.use.asked_deck, deck, viewer=connection.user_id
        )
        if asked is None:
            await socket.close(code=POLICY_VIOLATION)
            return
        await _talk_lease(
            socket,
            _Talk(
                asked=asked,
                connection=connection,
                language=language,
                controls_language=language_of(socket, sessions),
            ),
            surface=surface,
            guard=guard,
        )

    return _talk


@dataclass(frozen=True, slots=True, kw_only=True)
class _Talk:
    """A talk that passed admission: its deck, its person and its languages."""

    asked: AskedDeck
    connection: SignedIn
    language: str
    controls_language: Language


async def _talk_lease(
    socket: WebSocket,
    talk: _Talk,
    *,
    surface: CoPresenterSurface,
    guard: SessionGuard,
) -> None:
    session_id = talk.connection.session_id
    session_watch = asyncio.create_task(
        watch_session(socket, session_id=session_id, guard=guard)
    )
    try:
        tokens = await read_tokens(
            socket,
            session_id=session_id,
            use=surface.use,
            session_watch=session_watch,
        )
        if tokens is not None:
            await _talking(
                socket, talk, tokens, surface=surface, session_watch=session_watch
            )
    except WebSocketDisconnect:
        return
    except RuntimeError, ExceptionGroup:
        # The loop raises its unexpected failures inside an ExceptionGroup.
        with suppress(RuntimeError):
            await socket.close(code=INTERNAL_ERROR)
    finally:
        await finish_tasks((session_watch,))


async def _talking(
    socket: WebSocket,
    talk: _Talk,
    tokens: SpeechTokenSource,
    *,
    surface: CoPresenterSurface,
    session_watch: asyncio.Task[None],
) -> None:
    """Hear and answer until the talk ends; a session end ends it in order."""
    down = _DownFrames(socket)
    hearing = _TalkHearing()
    decisions = _Decisions(down)
    browser = _TalkBrowser(down, decisions.first_output)
    ended = asyncio.Event()

    async def heard(transcript: HearingTranscript) -> None:  # NOSONAR(S7503)
        down.send(
            {
                "kind": "heard",
                "text": transcript.text,
                "final": transcript.final,
                "language": transcript.language,
            }
        )
        if transcript.final:
            hearing.hear(transcript)

    use = surface.use
    sender = asyncio.create_task(down.send_all())
    relay = asyncio.create_task(
        use.relay_hearing(
            socket,
            talk.language,
            tokens,
            ended=ended,
            heard=heard,
            text=_handler_of(browser, hearing),
        )
    )
    talking = asyncio.create_task(
        use.talk(
            talk.asked,
            session_id=talk.connection.session_id,
            subject=talk.connection.user_id,
            role=talk.connection.person.role,
            controls_language=talk.controls_language,
            limiter=surface.limiter,
            browser=browser,
            hearing=hearing,
            report=decisions,
        )
    )
    try:
        await asyncio.wait(
            {relay, talking, session_watch}, return_when=asyncio.FIRST_COMPLETED
        )
        if session_watch.done():
            observe_session_end(session_watch)
            await _cut_talk(talking, decisions, _Cut.SESSION_ENDED)
            down.send(_error_frame(AnswerStopReason.SESSION_ENDED))
            await down.drained()
            ended.set()
            await relay
        elif relay.done():
            await _relay_ended(socket, relay, talking, hearing, decisions)
        else:
            await _loop_ended(talking, relay, decisions)
    finally:
        await finish_tasks((talking, relay, sender))


async def _cut_talk(
    talking: asyncio.Task[None], decisions: _Decisions, how: _Cut
) -> None:
    """End the model's loop now, with the lease free, and the decision it cut."""
    talking.cancel()
    await asyncio.wait({talking})
    decisions.cut(how)


async def _relay_ended(
    socket: WebSocket,
    relay: asyncio.Task[None],
    talking: asyncio.Task[None],
    hearing: _TalkHearing,
    decisions: _Decisions,
) -> None:
    """The hearing is over: the service closed, the browser left or it broke a rule."""
    failure = relay.exception()
    if failure is None:
        hearing.end()
        await talking
        decisions.cut(_Cut.CANCELLED)
        return
    await _cut_talk(talking, decisions, _Cut.CANCELLED)
    if isinstance(failure, UnreadableTalkMessageError):
        await socket.close(code=POLICY_VIOLATION)
        return
    raise failure


async def _loop_ended(
    talking: asyncio.Task[None], relay: asyncio.Task[None], decisions: _Decisions
) -> None:
    """The model's loop ended before the hearing did: it raised."""
    decisions.cut(_Cut.UNEXPECTED)
    await finish_tasks((relay,))
    talking.result()
