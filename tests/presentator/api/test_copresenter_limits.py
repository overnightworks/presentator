"""The ask limits of the co-presenter: size, language, rate, one answer."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field, replace
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

import pytest
from fastapi import FastAPI

from presentator.agent.backend import gateway_answerer
from presentator.api.copresenter import (
    MAX_SAID_LENGTH,
    CoPresenterSurface,
    add_copresenter_routes,
)
from presentator.contracts.copresenter import DeckText, RetryAfter
from presentator.contracts.models import Role, User
from tests.presentator.agent.support.transports import HeldTransport
from tests.presentator.application.support.ask_leases import (
    FakeAskLimiter,
    FakeAskTicket,
    UnavailableAskLimiter,
)
from tests.presentator.application.support.fakes import (
    FakeSessionRecordStore,
    FakeUserStore,
    FrozenClock,
)
from tests.support.copresenter_support import (
    A_DECK,
    AN_ANSWER_DEADLINE_SECONDS,
    ASK,
    QUESTION,
    SPOKEN_QUESTION,
    TALK_ORIGIN,
    PrivateFakes,
    a_copresenter_talk_host,
    a_use,
    ask,
    the_admins_decks,
)
from tests.support.lobby import (
    A_TALK_ADDRESS,
    ADMIN,
    NOW,
    a_relying_party,
    an_agent_config,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from httpx2 import Response
    from starlette.types import ASGIApp, Message, Scope

    from presentator.ports.copresenter import AskLimiter, AskTicket


@pytest.mark.parametrize(
    "invalid",
    [
        {"said": "x" * (MAX_SAID_LENGTH + 1)},
        {"language": "fr"},
        {"language": "DE"},
    ],
    ids=["too-long", "foreign-language", "upper-case-language"],
)
def test_ask_refuses_an_overlong_or_foreign_language_question_without_a_turn(
    invalid: dict[str, object],
) -> None:
    lobby, private, _clock = a_copresenter_talk_host()
    client = lobby.client
    lobby.sign_in()

    response = ask(client, {**QUESTION, **invalid})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert private.answers.messages == []


@pytest.mark.parametrize(
    ("allowed", "language_handed_on"),
    [
        ({"said": "x" * MAX_SAID_LENGTH}, "de"),
        ({"language": "en"}, "en"),
        ({"language": None}, "de"),
        ({"language": ""}, "de"),
    ],
    ids=[
        "longest-said",
        "english",
        "no-language-means-the-default",
        "empty-language-means-the-default-never-an-empty-string",
    ],
)
def test_ask_accepts_the_limit_and_the_offered_languages(
    allowed: dict[str, object], language_handed_on: str
) -> None:
    lobby, private, _clock = a_copresenter_talk_host()
    client = lobby.client
    lobby.sign_in()

    response = ask(client, {**QUESTION, **allowed})

    assert response.status_code == HTTPStatus.OK
    assert [language for _text, language in private.speaking.spoken] == [
        language_handed_on
    ]


@pytest.mark.parametrize(
    ("deck_language", "heard_language", "reads_in", "answer_language", "named"),
    [
        ("de", "de", "en", "de", "German"),
        ("de", "en", "de", "en", "English"),
        ("en", "de", "en", "de", "German"),
        ("en", "en", "de", "en", "English"),
        ("de", None, "en", "en", "English"),
        ("en", None, "de", "de", "German"),
    ],
)
def test_the_answer_is_written_and_spoken_in_the_language_the_question_was_heard_in(
    deck_language: str,
    heard_language: str | None,
    reads_in: str,
    answer_language: str,
    named: str,
) -> None:
    """CP12: heard, else the person's language of the controls, never the deck's."""
    lobby, private, _clock = a_copresenter_talk_host()
    private.texts.text = DeckText(title="t", language=deck_language, slides=())
    lobby.sign_in()

    response = lobby.client.post(
        "/copresenter/ask",
        json={**QUESTION, "language": heard_language},
        headers={"accept-language": reads_in},
    )

    assert response.status_code == HTTPStatus.OK
    (message,) = private.answers.messages
    assert named in message
    assert [language for _text, language in private.speaking.spoken] == [
        answer_language
    ]


def test_a_question_whose_lease_cannot_be_told_gets_503_and_no_turn() -> None:
    lobby, private, _clock = a_copresenter_talk_host(limiter=UnavailableAskLimiter())
    lobby.sign_in()

    response = ask(lobby.client)

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert private.answers.messages == []


def test_a_second_question_while_the_first_streams_gets_429_and_no_turn() -> None:
    lobby, private, _clock = a_copresenter_talk_host()
    client = lobby.client
    lobby.sign_in()
    second: list[Response] = []

    def ask_again_mid_answer(index: int) -> None:
        if index == 0 and not second:
            second.append(ask(client))

    private.answers.busy_events = 3
    private.answers.event_hook = ask_again_mid_answer

    first = ask(client)
    after = ask(client)

    assert first.status_code == HTTPStatus.OK
    assert [response.status_code for response in second] == [
        HTTPStatus.TOO_MANY_REQUESTS
    ]
    assert second[0].headers["retry-after"] == "1"
    assert after.status_code == HTTPStatus.OK
    assert len(private.answers.messages) == len([first, after])


_A_SHORT_DEADLINE_SECONDS = 0.05


@dataclass(frozen=True)
class FixedSession:
    """The admitted session of a browser."""

    session_id: str = "session"
    user_id: str = ADMIN
    person: User = field(
        default_factory=lambda: User(id=ADMIN, username="admin", role=Role.ADMIN)
    )


def test_a_disconnect_before_the_answer_streams_still_frees_the_session() -> None:
    limiter = FakeAskLimiter()
    private = PrivateFakes()
    app = FastAPI()
    add_copresenter_routes(
        app,
        surface=CoPresenterSurface(
            use=a_use(private, the_admins_decks()),
            limiter=limiter,
            browser_origin=TALK_ORIGIN,
            deadline_seconds=AN_ANSWER_DEADLINE_SECONDS,
        ),
        admission=lambda _connection: FixedSession(),
        sessions=FakeSessionRecordStore(users=FakeUserStore()),
    )

    asyncio.run(_ask_and_disconnect_before_the_body_is_sent(app))

    assert private.answers.messages == []
    assert isinstance(limiter.admit(FixedSession().session_id), FakeAskTicket)


async def _ask_and_disconnect_before_the_body_is_sent(app: ASGIApp) -> None:
    messages: list[Message] = [
        {"type": "http.request", "body": json.dumps(QUESTION).encode()},
        {"type": "http.disconnect"},
    ]

    async def receive() -> Message:
        return messages.pop(0) if messages else await asyncio.Future()

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            await asyncio.Future()

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/copresenter/ask",
        "raw_path": b"/copresenter/ask",
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 443),
        "state": {},
    }

    await app(scope, receive, send)


def _a_hanging_app(
    gateway: HeldTransport,
    limiter: AskLimiter,
    admission: Callable[[object], FixedSession],
    fakes: PrivateFakes | None = None,
) -> FastAPI:
    app = FastAPI()
    add_copresenter_routes(
        app,
        surface=CoPresenterSurface(
            use=replace(
                a_use(fakes or PrivateFakes(), the_admins_decks()),
                answers=gateway_answerer(
                    an_agent_config(),
                    a_relying_party(FrozenClock(instant=NOW)),
                    transport=gateway,
                ),
            ),
            limiter=limiter,
            browser_origin=A_TALK_ADDRESS,
            deadline_seconds=AN_ANSWER_DEADLINE_SECONDS,
        ),
        admission=admission,
        sessions=FakeSessionRecordStore(users=FakeUserStore()),
    )
    return app


def test_a_browser_that_leaves_mid_answer_closes_the_backend_and_calls_no_more() -> (
    None
):
    """C9: the held gateway call is cancelled, none follows, and the ticket is freed."""
    gateway = HeldTransport()
    limiter = FakeAskLimiter()
    app = _a_hanging_app(gateway, limiter, lambda _connection: FixedSession())

    asyncio.run(_ask_and_leave_while_the_gateway_holds(app, gateway))

    assert gateway.cancelled
    assert len(gateway.requests) == 1
    assert isinstance(limiter.admit(FixedSession().session_id), FakeAskTicket)


def test_the_admission_runs_off_the_event_loop_thread() -> None:
    """Decision 5: the session read of the admission belongs to the thread hop."""
    admitted_on: list[int] = []

    def admission(_connection: object) -> FixedSession:
        admitted_on.append(threading.get_ident())
        return FixedSession()

    gateway = HeldTransport()
    app = _a_hanging_app(gateway, FakeAskLimiter(), admission)

    asyncio.run(_ask_and_leave_while_the_gateway_holds(app, gateway))

    assert admitted_on
    assert threading.get_ident() not in admitted_on


@dataclass
class _ThreadRecordingLimiter:
    """The in-memory lease, noting the thread each release runs on."""

    released_on: list[int] = field(default_factory=list[int])
    _inner: FakeAskLimiter = field(default_factory=FakeAskLimiter)

    def admit(self, session_id: str) -> AskTicket | RetryAfter:
        admitted = self._inner.admit(session_id)
        if isinstance(admitted, RetryAfter):
            return admitted
        return _RecordingTicket(admitted, self.released_on)


@dataclass(frozen=True)
class _RecordingTicket:
    _inner: FakeAskTicket
    _released_on: list[int]

    @property
    def session_id(self) -> str:
        return self._inner.session_id

    def release(self) -> None:
        self._released_on.append(threading.get_ident())
        self._inner.release()


@pytest.mark.parametrize(
    "person_access_unavailable", [False, True], ids=["streams", "never-streams"]
)
def test_the_release_of_an_ask_runs_off_the_event_loop_thread(
    *, person_access_unavailable: bool
) -> None:
    """The release asks the shared Redis, which holds its thread while it is slow."""
    gateway = HeldTransport()
    limiter = _ThreadRecordingLimiter()
    fakes = PrivateFakes()
    fakes.person_access.unavailable = person_access_unavailable
    app = _a_hanging_app(gateway, limiter, lambda _connection: FixedSession(), fakes)

    asyncio.run(_ask_and_leave_while_the_gateway_holds(app, gateway))

    assert limiter.released_on
    assert threading.get_ident() not in limiter.released_on


def test_an_answer_that_outruns_the_deadline_ends_visibly_and_frees_the_lease() -> None:
    limiter = FakeAskLimiter()
    lobby, private, _clock = a_copresenter_talk_host(
        limiter=limiter, deadline_seconds=_A_SHORT_DEADLINE_SECONDS
    )
    lobby.sign_in()
    private.answers.hold = True

    outrun = ask(lobby.client)
    private.answers.hold = False
    next_answer = ask(lobby.client)

    assert outrun.status_code == HTTPStatus.OK
    assert "event: error" in outrun.text
    assert "event: done" in next_answer.text


async def _ask_and_leave_while_the_gateway_holds(
    app: ASGIApp, gateway: HeldTransport
) -> None:
    messages: list[Message] = [
        {
            "type": "http.request",
            "body": json.dumps({**SPOKEN_QUESTION, "deck": A_DECK}).encode(),
        }
    ]

    async def receive() -> Message:
        if messages:
            return messages.pop(0)
        await gateway.arrived.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        del message

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": ASK,
        "raw_path": ASK.encode(),
        "query_string": b"",
        "headers": [
            (b"host", b"talk.testserver"),
            (b"content-type", b"application/json"),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 443),
        "state": {},
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=5)


class _SteppedLoop(asyncio.SelectorEventLoop):
    """An event loop whose clock moves only when a test moves it."""

    def __init__(self) -> None:
        super().__init__()
        self.now = 1000.0

    def time(self) -> float:
        return self.now


@pytest.mark.parametrize("deltas", [["Eins ", "Zwei "], ["Eins "]])
def test_a_send_that_stalls_past_the_deadline_ends_with_the_failure_and_frees_lease(
    deltas: list[str],
) -> None:
    """The next event is already available when the stalled send resumes."""
    limiter = FakeAskLimiter()
    private = PrivateFakes()
    private.answers.deltas = deltas
    app = FastAPI()
    add_copresenter_routes(
        app,
        surface=CoPresenterSurface(
            use=a_use(private, the_admins_decks()),
            limiter=limiter,
            browser_origin=A_TALK_ADDRESS,
            deadline_seconds=AN_ANSWER_DEADLINE_SECONDS,
        ),
        admission=lambda _connection: FixedSession(),
        sessions=FakeSessionRecordStore(users=FakeUserStore()),
    )

    with asyncio.Runner(loop_factory=_SteppedLoop) as runner:
        loop = cast("_SteppedLoop", runner.get_loop())
        body = runner.run(_ask_while_the_first_send_outlasts_the_deadline(app, loop))

    assert body.count("event: text") == 1
    assert "Zwei" not in body
    assert "event: done" not in body
    assert body.count("event: error") == 1
    assert private.answers.exits == 1
    assert private.answers.reader_closed
    assert isinstance(limiter.admit(FixedSession().session_id), FakeAskTicket)


async def _ask_while_the_first_send_outlasts_the_deadline(
    app: ASGIApp, loop: _SteppedLoop
) -> str:
    messages: list[Message] = [
        {
            "type": "http.request",
            "body": json.dumps({**SPOKEN_QUESTION, "deck": A_DECK}).encode(),
        }
    ]
    sent: list[bytes] = []

    async def receive() -> Message:
        if messages:
            return messages.pop(0)
        return await asyncio.Future()

    async def send(message: Message) -> None:
        if message["type"] != "http.response.body":
            return
        sent.append(message.get("body", b""))
        if len(sent) == 1:
            loop.now += AN_ANSWER_DEADLINE_SECONDS + 1

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": ASK,
        "raw_path": ASK.encode(),
        "query_string": b"",
        "headers": [
            (b"host", b"talk.testserver"),
            (b"content-type", b"application/json"),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 443),
        "state": {},
    }
    await app(scope, receive, send)
    return b"".join(sent).decode()
