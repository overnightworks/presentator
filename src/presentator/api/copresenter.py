"""Authenticated browser routes for the private co-presenter."""

from __future__ import annotations

import asyncio
import json
from base64 import b64encode
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
)
from contextlib import suppress
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Final, Protocol, assert_never

from fastapi import APIRouter, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection
from starlette.responses import Response

from presentator.api.session_api import language_of
from presentator.application.copresenter import RetryAfter
from presentator.contracts.copresenter import (
    AccessRefusal,
    AnswerEvent,
    AnswerStopReason,
    AnswerText,
    AskedDeck,
    Audio,
    BudgetSpentError,
    CoPresenterUnavailableError,
    Done,
    GatewayBusyError,
    HearingTranscript,
    NotEnabledError,
    Question,
    Sentence,
    ShowSlide,
    SpeechTokens,
)
from presentator.contracts.decks import MAXIMUM_NAME_BYTES
from presentator.contracts.voice import SampleLanguage

if TYPE_CHECKING:
    from webauth.ports import SessionRecordStore

    from presentator.application.copresenter import (
        AskLimiter,
        AskTicket,
        CoPresenterUse,
        PreparedAsk,
    )
    from presentator.application.preferences import Preferences
    from presentator.contracts.models import Role, User
    from presentator.contracts.words import Language

_CHECK_INTERVAL_SECONDS: Final = 0.5
_POLICY_VIOLATION: Final = 1008
_INTERNAL_ERROR: Final = 1011
_GENERIC_ANSWER_FAILURE: Final = "the answer could not be spoken"
MAX_SAID_LENGTH: Final = 2_000


class SignedIn(Protocol):
    """The admission fact operation watchers retain."""

    @property
    def session_id(self) -> str:
        """The authoritative session id retained by an operation."""
        ...

    @property
    def user_id(self) -> str:
        """The account the session belongs to."""
        ...

    @property
    def person(self) -> User:
        """The account record the session belongs to."""
        ...


Admission = Callable[[HTTPConnection], SignedIn | None]
Delay = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True, kw_only=True)
class CoPresenterSurface:
    """The use case and browser origin installed on the talk host."""

    use: CoPresenterUse
    limiter: AskLimiter
    preferences: Preferences
    browser_origin: str
    delay: Delay = asyncio.sleep


@dataclass(frozen=True, slots=True, kw_only=True)
class _SessionGuard:
    admission: Admission
    delay: Delay

    def same_session(self, connection: HTTPConnection, session_id: str) -> bool:
        admitted = self.admission(connection)
        return admitted is not None and admitted.session_id == session_id

    async def wait(self) -> None:
        await self.delay(_CHECK_INTERVAL_SECONDS)


class AskRequest(BaseModel, extra="forbid"):
    """The only browser fields a private answer may receive."""

    said: str = Field(min_length=1, max_length=MAX_SAID_LENGTH)
    slide: int = Field(ge=1)
    deck: str = Field(default="", max_length=MAXIMUM_NAME_BYTES)
    language: SampleLanguage | None = None

    @field_validator("language", mode="before")
    @staticmethod
    def empty_language_is_no_language(value: object) -> object:
        """Treat the browser's empty language field as no language chosen."""
        return None if value == "" else value


def add_copresenter_routes(
    app: FastAPI,
    *,
    surface: CoPresenterSurface,
    admission: Admission,
    sessions: SessionRecordStore,
) -> None:
    """Add the exact signed-in co-presenter allowlist to the talk host."""
    router = APIRouter(prefix="/copresenter")
    use = surface.use
    guard = _SessionGuard(admission=admission, delay=surface.delay)

    async def _hear(socket: WebSocket, language: str = "de") -> None:
        if (
            socket.headers.get("origin") != surface.browser_origin
            or any(key != "language" for key in socket.query_params)
            or len(socket.query_params.getlist("language")) > 1
        ):
            await socket.close(code=_POLICY_VIOLATION)
            return
        connection = admission(socket)
        if connection is None:
            await socket.close(code=_POLICY_VIOLATION)
            return
        await socket.accept()
        await _hearing_lease(
            socket,
            language=language,
            session_id=connection.session_id,
            use=use,
            guard=guard,
        )

    router.add_api_route(
        "/who",
        _who_route(
            use,
            admission=admission,
            interface_language=lambda request: language_of(
                request, surface.preferences, sessions
            ),
        ),
        methods=["GET"],
    )
    router.add_api_route(
        "/ask", _ask_route(surface, admission=admission, guard=guard), methods=["POST"]
    )
    router.add_api_websocket_route("/hear", _hear)
    app.include_router(router)


def _who_route(
    use: CoPresenterUse,
    *,
    admission: Admission,
    interface_language: Callable[[Request], Language],
) -> Callable[[Request, str], Awaitable[JSONResponse]]:
    async def _who(request: Request, deck: str = "") -> JSONResponse:
        connection = admission(request)
        if connection is None:
            return _not_authenticated()
        asked = await run_in_threadpool(use.asked_deck, deck, viewer=connection.user_id)
        if asked is None:
            return _deck_not_found()
        try:
            language = await use.deck_language(asked)
            ready = await use.readiness()
        except CoPresenterUnavailableError:
            return _unavailable()
        return JSONResponse(
            {
                "language": language.value,
                "interface_language": interface_language(request),
                "speech": {"hearing": {"ready": ready.local_hearing_ready}},
            },
        )

    return _who


@dataclass(frozen=True, slots=True, kw_only=True)
class _Ask:
    """A question that passed admission, with the deck it is about and its ticket."""

    asked: AskedDeck
    question: Question
    ticket: AskTicket
    subject: str
    role: Role


def _ask_route(
    surface: CoPresenterSurface, *, admission: Admission, guard: _SessionGuard
) -> Callable[[Request, AskRequest], Awaitable[Response]]:
    async def _ask(request: Request, question: AskRequest) -> Response:
        admitted = await run_in_threadpool(
            _admit, surface, admission, request, question
        )
        if isinstance(admitted, Response):
            return admitted
        streaming = False
        try:
            prepared = await _prepare(surface.use, admitted)
            if isinstance(prepared, Response):
                return prepared
            response = StreamingResponse(
                _answer_events(
                    request,
                    ask=admitted,
                    prepared=prepared,
                    use=surface.use,
                    guard=guard,
                ),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                background=BackgroundTask(admitted.ticket.release),
            )
            streaming = True
            return response
        finally:
            if not streaming:
                admitted.ticket.release()

    return _ask


def _admit(
    surface: CoPresenterSurface,
    admission: Admission,
    request: Request,
    question: AskRequest,
) -> _Ask | Response:
    connection = admission(request)
    if connection is None:
        return _not_authenticated()
    asked = surface.use.asked_deck(question.deck, viewer=connection.user_id)
    if asked is None:
        return _deck_not_found()
    ticket = surface.limiter.admit(connection.session_id)
    if isinstance(ticket, RetryAfter):
        return JSONResponse(
            {"detail": "Too many questions"},
            HTTPStatus.TOO_MANY_REQUESTS,
            headers={"Retry-After": str(ticket.seconds)},
        )
    return _Ask(
        asked=asked,
        question=Question(
            said=question.said,
            slide=question.slide,
            language=question.language and question.language.value,
        ),
        ticket=ticket,
        subject=connection.user_id,
        role=connection.person.role,
    )


async def _prepare(use: CoPresenterUse, ask: _Ask) -> PreparedAsk | Response:
    try:
        prepared = await use.prepare(
            ask.asked,
            session_id=ask.ticket.session_id,
            subject=ask.subject,
            role=ask.role,
        )
    except CoPresenterUnavailableError:
        return _unavailable()
    if isinstance(prepared, AccessRefusal):
        return _access_refusal(prepared)
    return prepared


def _deck_not_found() -> JSONResponse:
    return JSONResponse({"detail": "Not found"}, HTTPStatus.NOT_FOUND)


def _unavailable() -> JSONResponse:
    return JSONResponse(
        {"detail": "Co-presenter unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE
    )


def _access_refusal(refusal: AccessRefusal) -> JSONResponse:
    match refusal:
        case AccessRefusal.SIGN_IN_AGAIN:
            return JSONResponse({"detail": "Sign in again"}, HTTPStatus.UNAUTHORIZED)
        case AccessRefusal.SESSION_ENDED:
            return _not_authenticated()
        case AccessRefusal.ISSUER_UNAVAILABLE:
            return _unavailable()
        case _:
            assert_never(refusal)


def _not_authenticated() -> JSONResponse:
    return JSONResponse({"detail": "Not authenticated"}, HTTPStatus.UNAUTHORIZED)


async def _answer_events(
    request: Request,
    *,
    ask: _Ask,
    prepared: PreparedAsk,
    use: CoPresenterUse,
    guard: _SessionGuard,
) -> AsyncIterator[bytes]:
    session_watch = asyncio.create_task(
        _watch_session(request, session_id=ask.ticket.session_id, guard=guard)
    )
    try:
        try:
            async with use.answer(prepared, ask.question) as events:
                next_event = asyncio.create_task(_next_answer_event(events))
                try:
                    while True:
                        done, _pending = await asyncio.wait(
                            {next_event, session_watch},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if session_watch in done:
                            _observe_session_end(session_watch)
                            return
                        event = next_event.result()
                        yield _sse(event)
                        if isinstance(event, Done):
                            return
                        next_event = asyncio.create_task(_next_answer_event(events))
                finally:
                    await _finish_tasks((next_event,))
        except NotEnabledError:
            yield _error_sse(AnswerStopReason.NOT_ENABLED)
        except GatewayBusyError as busy:
            yield _error_sse(AnswerStopReason.BUSY, retry_after_s=busy.retry_after_s)
        except BudgetSpentError:
            yield _error_sse(AnswerStopReason.BUDGET_SPENT)
        except CoPresenterUnavailableError:
            yield _error_sse()
    finally:
        ask.ticket.release()
        await _finish_tasks((session_watch,))


async def _hearing_lease(
    socket: WebSocket,
    *,
    language: str,
    session_id: str,
    use: CoPresenterUse,
    guard: _SessionGuard,
) -> None:
    session_watch = asyncio.create_task(
        _watch_session(socket, session_id=session_id, guard=guard)
    )
    try:
        tokens = await _read_tokens(
            socket, session_id=session_id, use=use, session_watch=session_watch
        )
        if tokens is not None:
            await _relay(
                socket,
                language=language,
                tokens=tokens,
                use=use,
                session_watch=session_watch,
            )
    except WebSocketDisconnect:
        return
    except RuntimeError:
        with suppress(RuntimeError):
            await socket.close(code=_INTERNAL_ERROR)
    finally:
        await _finish_tasks((session_watch,))


async def _read_tokens(
    socket: WebSocket,
    *,
    session_id: str,
    use: CoPresenterUse,
    session_watch: asyncio.Task[None],
) -> SpeechTokens | None:
    """The session's tokens, or None once the socket has been closed instead."""
    read = asyncio.create_task(use.hearing_tokens(session_id))
    try:
        await asyncio.wait({read, session_watch}, return_when=asyncio.FIRST_COMPLETED)
        if not read.done():
            _observe_session_end(session_watch)
            await socket.close(code=_POLICY_VIOLATION)
            return None
        try:
            return read.result()
        except CoPresenterUnavailableError:
            await use.refuse_hearing(socket)
            return None
    finally:
        await _finish_tasks((read,))


async def _relay(
    socket: WebSocket,
    *,
    language: str,
    tokens: SpeechTokens,
    use: CoPresenterUse,
    session_watch: asyncio.Task[None],
) -> None:
    """Hear until the hearing ends; a session end sets the signal that ends it."""
    ended = asyncio.Event()

    async def heard(transcript: HearingTranscript) -> None:
        await socket.send_json(
            {
                "text": transcript.text,
                "final": transcript.final,
                "language": transcript.language and transcript.language.value,
            }
        )

    hearing = asyncio.create_task(
        use.relay_hearing(socket, language, tokens, ended=ended, heard=heard)
    )
    try:
        await asyncio.wait(
            {hearing, session_watch}, return_when=asyncio.FIRST_COMPLETED
        )
        if not hearing.done():
            _observe_session_end(session_watch)
            ended.set()
        await hearing
    finally:
        await _finish_tasks((hearing,))


async def _watch_session(
    connection: HTTPConnection,
    *,
    session_id: str,
    guard: _SessionGuard,
) -> None:
    while True:
        await guard.wait()
        if not guard.same_session(connection, session_id):
            return


def _observe_session_end(session_watch: asyncio.Task[None]) -> None:
    session_watch.exception()


async def _next_answer_event(events: AsyncIterator[AnswerEvent]) -> AnswerEvent:
    return await anext(events)


async def _finish_tasks(tasks: Iterable[asyncio.Task[object]]) -> None:
    owned = tuple(tasks)
    for task in owned:
        if not task.done():
            task.cancel()
    await asyncio.wait(owned)
    for task in owned:
        if not task.cancelled():
            task.exception()


def _sse(event: AnswerEvent) -> bytes:
    if isinstance(event, AnswerText):
        return _generated_sse("text", {"text": event.text})
    if isinstance(event, Sentence):
        return _generated_sse("sentence", {"text": event.text})
    if isinstance(event, Audio):
        return _generated_sse(
            "audio",
            {"text": event.text, "wav_b64": b64encode(event.wav).decode("ascii")},
        )
    if isinstance(event, ShowSlide):
        return _encoded_sse("slide", {"number": event.number})
    return _generated_sse("done", {"text": event.text})


def _generated_sse(event: str, payload: dict[str, object]) -> bytes:
    return _encoded_sse(event, {**payload, "ai_generated": True})


def _error_sse(
    reason: AnswerStopReason | None = None, *, retry_after_s: int | None = None
) -> bytes:
    payload: dict[str, object] = {"message": _GENERIC_ANSWER_FAILURE}
    if reason is not None:
        payload["reason"] = reason.value
    if retry_after_s is not None:
        payload["retry_after_s"] = retry_after_s
    return _encoded_sse("error", payload)


def _encoded_sse(event: str, payload: dict[str, object]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()
