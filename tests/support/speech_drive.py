"""The co-presenter talk host with its real speech adapters against the fake service."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import threading
from contextlib import asynccontextmanager, closing, contextmanager, suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from agent_runtime_client.backend.fake_gateway import FakeGateway, completion
from speech_runtime_client import wire
from speech_runtime_client.outcomes import PastDeadline
from starlette.websockets import WebSocketDisconnect

from presentator.adapters.sentences import split_sentences
from presentator.adapters.session_access import SessionAccess
from presentator.adapters.speech import SpeechHearing, SpeechSpeaking
from presentator.agent.backend import gateway_answerer
from presentator.api.copresenter import CoPresenterSurface
from presentator.application.copresenter import CoPresenterUse
from presentator.contracts.copresenter import HearingTranscript, SpeechTokens
from tests.presentator.application.support.fakes import FakeSourceStore, FrozenClock
from tests.support.copresenter_support import (
    A_DECK_WORDS,
    A_SOURCE,
    ACCESS,
    API_MEMBER_GROUPS,
    HEARING,
    REFRESH,
    ROTATED,
    SPOKEN_QUESTION,
    THE_ASKED_DECK,
    Gateway,
    ManualDeadline,
    PrivateFakes,
    RecordingLimiter,
    RecordingLogs,
    Run,
    WitnessedAccess,
    WitnessedAnswers,
    a_good_gateway,
    nothing_happens,
    the_refresh_token_is_revoked,
    the_session_ends_during_the_grant,
    the_token_thread_fails,
)
from tests.support.deck_text_support import FakeDeckTexts
from tests.support.leak_support import everything_shown
from tests.support.lobby import (
    A_BROWSERS_HEADERS,
    A_SPEECH_SCOPE,
    A_SPEECH_VOICE,
    A_TALK_ADDRESS,
    NOW,
    an_agent_config,
)
from tests.support.speech_service import (
    AN_ITEM,
    HEALTH_FAILURES,
    MALFORMED_PEERS,
    PLANTED,
    SPEAK_FAILURES,
    Answer,
    FakeSpeechService,
    Hear,
    RawPeer,
    Served,
    a_completed,
    a_delta,
    a_failed_item,
    a_loopback_server,
    a_marked_error_event,
    a_marked_failed_item,
    a_served_origin,
    after_the_first_audio,
    an_error_event,
    busy_at_the_upgrade,
    crashing_after_the_first_audio,
    denying,
    nobody_listens,
)
from tests.support.talk_support import A, Hosts, two_hosts

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Generator, Mapping, Sequence
    from pathlib import Path

    from fastapi.testclient import TestClient
    from httpx2 import Response
    from starlette.types import Message, Scope

    from presentator.adapters.identity import SqliteSessionRecordStore
    from presentator.api.auth import InstalledAuth
    from presentator.contracts.copresenter import CoPresenterReadiness
    from presentator.ports.copresenter import PrivateHearing, Speaking
    from presentator.ports.copresenter import SpeechHearing as SpeechHearingPort
    from tests.presentator.application.support.fakes import FakeDeckStore

_WIDE_OPEN_LOGGERS = (
    "presentator",
    "webauth",
    "agent_runtime_client",
    "speech_runtime_client",
    "httpx2",
    "httpcore2",
    "websockets",
    "wsproto",
)
WORKLOAD = "workload-token-MARKER"
THREAD_WAIT_SECONDS: Final = 10
ORIGIN: Final = {"origin": A_TALK_ADDRESS}
ANSWER_SENTENCES: Final = ("Antwort-MARKER eins.", "Antwort-MARKER zwei.")
HEARD_WORDS: Final = ("gehoert-MARKER-eins", "gehoert-MARKER-zwei")
_ANSWER_EVENTS: Final = frozenset({"text", "sentence", "audio", "done"})
_SSE_EVENT: Final = re.compile(
    r"^event: (?P<name>\w+)\ndata: (?P<data>.*)$", re.MULTILINE
)
_NO_HEARING_ERROR_KEY: Final = "error"
_REFRESH_COLUMN: Final = "refresh_token"
_USER_ID_COLUMN: Final = "user_id"
_ID_COLUMN: Final = "id"
_A_JWT_STARTS: Final = "eyJ"


@dataclass(frozen=True, kw_only=True)
class SpeechScenario:
    """What differs from a good instance: one factor at a time."""

    gateway: Callable[[], Gateway] = a_good_gateway
    service: FakeSpeechService = field(default_factory=FakeSpeechService)
    refresh_token: str | None = REFRESH
    arrange: Callable[[Hosts], None] = nothing_happens
    peer: Served | None = None
    bounded_calls_stall: bool = False


@dataclass(kw_only=True)
class SpeechInstance:
    """A signed-in admin's talk browser and everything that could see the use."""

    hosts: Hosts
    talk: TestClient
    service: FakeSpeechService
    gateway: Gateway
    limiter: RecordingLimiter
    deadline: ManualDeadline
    grants_before: list[str]
    errors: list[BaseException] = field(default_factory=list[BaseException])

    def run_of(self, response: Response, logged: list[logging.LogRecord]) -> Run:
        return Run(
            response,
            self.hosts,
            self.gateway,
            PrivateFakes(),
            self.talk,
            self.limiter,
            logged,
            self.grants_before,
            self.errors,
        )


def _past_deadline(*_args: object, **_kwargs: object) -> PastDeadline:
    return PastDeadline()


class WitnessedSpeaking:
    """The real speaking, keeping every error it lets out for the leak check."""

    def __init__(self, speaking: Speaking, errors: list[BaseException]) -> None:
        """Wrap this speaking."""
        self._speaking = speaking
        self._errors = errors

    async def speak(self, text: str, language: str, tokens: SpeechTokens) -> bytes:
        try:
            return await self._speaking.speak(text, language, tokens)
        except BaseException as error:
            self._errors.append(error)
            raise


class _WitnessedHearing:
    """One hearing, keeping every error it lets out for the leak check."""

    def __init__(self, hearing: PrivateHearing, errors: list[BaseException]) -> None:
        self._hearing = hearing
        self._errors = errors

    async def send_pcm(self, frame: bytes) -> None:
        try:
            await self._hearing.send_pcm(frame)
        except BaseException as error:
            self._errors.append(error)
            raise

    async def receive(self) -> HearingTranscript | None:
        try:
            return await self._hearing.receive()
        except BaseException as error:
            self._errors.append(error)
            raise

    async def aclose(self) -> None:
        await self._hearing.aclose()


class WitnessedSpeech:
    """The real hearing and readiness, keeping every error they let out."""

    def __init__(self, speech: SpeechHearingPort, errors: list[BaseException]) -> None:
        """Wrap this hearing."""
        self._speech = speech
        self._errors = errors

    async def readiness(self) -> CoPresenterReadiness:
        try:
            return await self._speech.readiness()
        except BaseException as error:
            self._errors.append(error)
            raise

    @asynccontextmanager
    async def hear(
        self, language: str, tokens: SpeechTokens
    ) -> AsyncGenerator[PrivateHearing]:
        try:
            async with self._speech.hear(language, tokens) as hearing:
                yield _WitnessedHearing(hearing, self._errors)
        except BaseException as error:
            self._errors.append(error)
            raise


def _real_speech_adapters(
    origin: str, *, bounded_calls_stall: bool
) -> tuple[SpeechHearing, SpeechSpeaking]:
    if bounded_calls_stall:
        return (
            SpeechHearing(
                origin, voice=A_SPEECH_VOICE, bounded_health_fetch=_past_deadline
            ),
            SpeechSpeaking(origin, voice=A_SPEECH_VOICE, bounded_speak=_past_deadline),
        )
    return (
        SpeechHearing(origin, voice=A_SPEECH_VOICE),
        SpeechSpeaking(origin, voice=A_SPEECH_VOICE),
    )


def a_surface_over(
    adapters: tuple[SpeechHearing, SpeechSpeaking],
    *,
    gateway: Gateway,
    limiter: RecordingLimiter,
    deadline: ManualDeadline,
    errors: list[BaseException],
) -> Callable[
    [FakeDeckStore, SqliteSessionRecordStore, InstalledAuth], CoPresenterSurface
]:
    """The talk host's surface over the real speech adapters and session access."""
    hearing, speaking = adapters

    def surface(
        store: FakeDeckStore, sessions: SqliteSessionRecordStore, auth: InstalledAuth
    ) -> CoPresenterSurface:
        return CoPresenterSurface(
            use=CoPresenterUse(
                answers=WitnessedAnswers(
                    gateway_answerer(
                        an_agent_config(), auth.relying_party, transport=gateway
                    ),
                    errors,
                ),
                speech=WitnessedSpeech(hearing, errors),
                decks=store,
                sources=FakeSourceStore(sources=[A_SOURCE]),
                texts=FakeDeckTexts(),
                boundaries=split_sentences,
                speaking=WitnessedSpeaking(speaking, errors),
                person_access=WitnessedAccess(
                    SessionAccess(
                        auth.relying_party,
                        sessions,
                        auth.config,
                        speech_scope=A_SPEECH_SCOPE,
                    ),
                    errors,
                ),
            ),
            limiter=limiter,
            browser_origin=A_TALK_ADDRESS,
            delay=deadline.wait,
        )

    return surface


@contextmanager
def a_speech_instance(
    tmp_path: Path, scenario: SpeechScenario | None = None
) -> Generator[SpeechInstance]:
    """Sign an admin in at the talk host, over the fake service, for the block."""
    scenario = scenario or SpeechScenario()
    limiter = RecordingLimiter(clock=FrozenClock(instant=NOW))
    deadline = ManualDeadline()
    gateway = scenario.gateway()
    errors: list[BaseException] = []
    serving = scenario.peer or (lambda: a_loopback_server(scenario.service.app()))
    with a_served_origin(serving) as origin:
        hosts = two_hosts(
            tmp_path,
            copresenter=a_surface_over(
                _real_speech_adapters(
                    origin, bounded_calls_stall=scenario.bounded_calls_stall
                ),
                gateway=gateway,
                limiter=limiter,
                deadline=deadline,
                errors=errors,
            ),
        )
        hosts.issuer.issue_person_access_token(ACCESS)
        hosts.issuer.rotate_refresh_token_to(ROTATED)
        hosts.issuer.workload_token = WORKLOAD
        if scenario.refresh_token is not None:
            hosts.issuer.issue_refresh_token(scenario.refresh_token)
        talk = hosts.browser(A_TALK_ADDRESS, headers=A_BROWSERS_HEADERS)
        hosts.sign_in(talk, A_TALK_ADDRESS, subject=A.id, groups=API_MEMBER_GROUPS)
        scenario.arrange(hosts)
        yield SpeechInstance(
            hosts=hosts,
            talk=talk,
            service=scenario.service,
            gateway=gateway,
            limiter=limiter,
            deadline=deadline,
            grants_before=list(hosts.issuer.grant_types),
            errors=errors,
        )


@contextmanager
def recorded_logs() -> Generator[list[logging.LogRecord]]:
    """Every record any logger raises in the block, with every logger at DEBUG."""
    logs = RecordingLogs()
    root = logging.getLogger()
    named = [logging.getLogger(name) for name in _WIDE_OPEN_LOGGERS]
    levels = [logger.level for logger in (root, *named)]
    root.addHandler(logs)
    root.setLevel(logging.DEBUG)
    for logger in named:
        logger.setLevel(logging.DEBUG)
    try:
        yield logs.records
    finally:
        root.removeHandler(logs)
        for logger, level in zip((root, *named), levels, strict=True):
            logger.setLevel(level)


def ask_over(
    instance: SpeechInstance, body: Mapping[str, object] = THE_ASKED_DECK
) -> Run:
    """One ask with every logger at DEBUG."""
    with recorded_logs() as logged:
        response = instance.talk.post("/copresenter/ask", json=body)
    return instance.run_of(response, logged)


def hear_and_record(
    instance: SpeechInstance,
) -> tuple[list[dict[str, object]], list[logging.LogRecord]]:
    """One lease until its error frame; every frame and every log record."""
    frames: list[dict[str, object]] = []
    with (
        recorded_logs() as logged,
        instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket,
    ):
        socket.send_bytes(b"first")
        with suppress(WebSocketDisconnect):
            while True:
                frame = socket.receive_json()
                frames.append(frame)
                if _NO_HEARING_ERROR_KEY in frame:
                    break
    return frames, list(logged)


def _every_marker(instance: SpeechInstance) -> tuple[str, ...]:
    """What no surface may show but where the contract says a value belongs."""
    return (
        ACCESS,
        WORKLOAD,
        REFRESH,
        ROTATED,
        *instance.hosts.issuer.issued_id_tokens,
        "Bearer ",
        _A_JWT_STARTS,
        A.id,
        *API_MEMBER_GROUPS,
        str(SPOKEN_QUESTION["said"]),
        *A_DECK_WORDS,
        PLANTED,
    )


def _absent(text: str, markers: Sequence[str]) -> None:
    for marker in markers:
        assert marker not in text


def _sse_events(text: str) -> list[tuple[str, str]]:
    return [
        (event["name"], json.dumps(json.loads(event["data"]), ensure_ascii=False))
        for event in _SSE_EVENT.finditer(text)
    ]


def _assert_the_body_keeps_the_answer_to_its_events(
    body: str, markers: Sequence[str]
) -> None:
    """The whole body shows no marker; only answer events may carry the sentences."""
    never_exempt = (*markers, *HEARD_WORDS)
    _absent(body, never_exempt)
    _absent(
        _SSE_EVENT.sub(
            lambda event: "" if event["name"] in _ANSWER_EVENTS else event[0], body
        ),
        ANSWER_SENTENCES,
    )
    for name, data in _sse_events(body):
        _absent(
            data,
            never_exempt
            if name in _ANSWER_EVENTS
            else (*never_exempt, *ANSWER_SENTENCES),
        )


def stored_session_rows(hosts: Hosts) -> list[dict[str, object]]:
    """Every stored session row, by column name."""
    with closing(sqlite3.connect(hosts.database)) as connection:
        cursor = connection.execute("SELECT * FROM sessions")
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _assert_rows_keep_only_their_own_values(
    hosts: Hosts, markers: Sequence[str]
) -> None:
    """A row's values show no marker, but for each column's own contract value."""
    # A session id is random text that may start like a JWT by chance alone.
    belonging = {
        _REFRESH_COLUMN: {REFRESH, ROTATED},
        _USER_ID_COLUMN: {A.id},
        _ID_COLUMN: {_A_JWT_STARTS},
    }
    for row in stored_session_rows(hosts):
        for column, value in row.items():
            for marker in markers:
                if marker in belonging.get(column, ()):
                    continue
                assert marker not in str(value), (column, marker)


def assert_nothing_shows(
    instance: SpeechInstance,
    logged: Sequence[logging.LogRecord],
    *,
    response: Response | None = None,
    frames: Sequence[Mapping[str, object]] = (),
) -> None:
    """No marker in a log, a caught error, the browser or a stored row."""
    markers = _every_marker(instance)
    words = (*ANSWER_SENTENCES, *HEARD_WORDS)
    _absent(everything_shown(logged, instance.errors), (*markers, *words))
    _assert_rows_keep_only_their_own_values(instance.hosts, (*markers, *words))
    if response is not None:
        _absent("\n".join(response.headers.values()), (*markers, *words))
        _assert_the_body_keeps_the_answer_to_its_events(response.text, markers)
    for frame in frames:
        exempt = () if _NO_HEARING_ERROR_KEY in frame else HEARD_WORDS
        text = json.dumps(frame, ensure_ascii=False)
        _absent(text, (*markers, *(w for w in words if w not in exempt)))


def the_marked_answer() -> Gateway:
    return FakeGateway(completion(" ".join(ANSWER_SENTENCES)))


def refusing_every_workload_grant(error: str) -> Callable[[Hosts], None]:
    def arrange(hosts: Hosts) -> None:
        hosts.issuer.workload_refusal = (error, PLANTED)

    return arrange


def _hearing_the_words() -> Hear:
    first, second = HEARD_WORDS
    return after_the_first_audio(
        a_delta(first), a_delta(second), a_completed(f"{first} {second}")
    )


def _with_service(
    service: Callable[[], FakeSpeechService],
) -> Callable[[], SpeechScenario]:
    return lambda: SpeechScenario(gateway=the_marked_answer, service=service())


def _with_speak_answer(answer: Answer) -> Callable[[], SpeechScenario]:
    return _with_service(lambda: FakeSpeechService(speak_answers=[answer]))


def _with_realtime(realtime: Hear) -> Callable[[], SpeechScenario]:
    return _with_service(lambda: FakeSpeechService(realtime=realtime))


def _with_health_answer(answer: Answer) -> Callable[[], SpeechScenario]:
    return _with_service(lambda: FakeSpeechService(health_answer=answer))


def _with_peer(peer: Served) -> Callable[[], SpeechScenario]:
    return lambda: SpeechScenario(gateway=the_marked_answer, peer=peer)


def _arranged(arrange: Callable[[Hosts], None]) -> Callable[[], SpeechScenario]:
    return lambda: SpeechScenario(gateway=the_marked_answer, arrange=arrange)


def _stalled() -> SpeechScenario:
    return SpeechScenario(gateway=the_marked_answer, bounded_calls_stall=True)


def _malformed_peer_cases() -> dict[str, Callable[[], SpeechScenario]]:
    return {
        f"peer-{name}": _with_peer(RawPeer(answer).serving)
        for name, answer in MALFORMED_PEERS.items()
    }


def _ended_before_any_service_is_asked() -> dict[str, Callable[[], SpeechScenario]]:
    return {
        "marked-issuer-refusal": _arranged(refusing_every_workload_grant(PLANTED)),
        "workload-scope-refused": _arranged(
            refusing_every_workload_grant("invalid_scope")
        ),
        "thread-raises-the-token": _arranged(the_token_thread_fails),
        "revoked-refresh-token": _arranged(the_refresh_token_is_revoked),
        "session-ends-during-the-grant": _arranged(the_session_ends_during_the_grant),
    }


LEAK_ASKS: Final[dict[str, Callable[[], SpeechScenario]]] = {
    "good": lambda: SpeechScenario(gateway=the_marked_answer),
    **{
        f"speak-{name}": _with_speak_answer(answer)
        for name, answer in SPEAK_FAILURES.items()
    },
    **_malformed_peer_cases(),
    "nobody-listens": _with_peer(nobody_listens),
    "stalled": _stalled,
    "unspeakable-remainder": lambda: SpeechScenario(
        gateway=lambda: FakeGateway(completion(f"{ANSWER_SENTENCES[0]} …"))
    ),
    **_ended_before_any_service_is_asked(),
}

LEAK_HEARINGS: Final[dict[str, Callable[[], SpeechScenario]]] = {
    "good": _with_realtime(_hearing_the_words()),
    "marked-error-event": _with_realtime(after_the_first_audio(a_marked_error_event())),
    "marked-failed-then-error": _with_realtime(
        after_the_first_audio(a_marked_failed_item(), a_marked_error_event())
    ),
    "busy-marked": _with_realtime(busy_at_the_upgrade(marked=True)),
    **{
        f"denied-{status}": _with_realtime(denying(status, marked=True))
        for status in (401, 403, 503)
    },
    **_malformed_peer_cases(),
    "nobody-listens": _with_peer(nobody_listens),
    **_ended_before_any_service_is_asked(),
}

LEAK_HEALTH: Final[dict[str, Callable[[], SpeechScenario]]] = {
    **{
        f"health-{name}": _with_health_answer(answer)
        for name, answer in HEALTH_FAILURES.items()
    },
    **_malformed_peer_cases(),
    "nobody-listens": _with_peer(nobody_listens),
    "stalled": _stalled,
}


def who_over(instance: SpeechInstance) -> tuple[Response, list[logging.LogRecord]]:
    """One `/who` read with every logger at DEBUG."""
    with recorded_logs() as logged:
        response = instance.talk.get("/copresenter/who", params={"deck": "a-deck"})
    return response, list(logged)


def _cookie_header(instance: SpeechInstance) -> bytes:
    return "; ".join(
        f"{cookie.name}={cookie.value}" for cookie in instance.talk.cookies.jar
    ).encode()


def _asked_scope(instance: SpeechInstance) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": urlsplit(A_TALK_ADDRESS).scheme,
        "path": "/copresenter/ask",
        "raw_path": b"/copresenter/ask",
        "query_string": b"",
        "headers": [
            (b"host", urlsplit(A_TALK_ADDRESS).netloc.encode()),
            (b"content-type", b"application/json"),
            *(
                (name.lower().encode(), value.encode())
                for name, value in A_BROWSERS_HEADERS.items()
            ),
            (b"cookie", _cookie_header(instance)),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 443),
        "state": {},
    }


async def ask_and_leave_at_the_first_speak(
    instance: SpeechInstance, arrived: threading.Event, release: threading.Event
) -> None:
    messages: list[Message] = [
        {"type": "http.request", "body": json.dumps(THE_ASKED_DECK).encode()}
    ]

    async def receive() -> Message:
        if messages:
            return messages.pop(0)
        await asyncio.to_thread(arrived.wait, THREAD_WAIT_SECONDS)
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        del message

    await asyncio.wait_for(
        instance.hosts.router(_asked_scope(instance), receive, send),
        timeout=THREAD_WAIT_SECONDS,
    )
    release.set()


def _transcripts(*shown: tuple[str, bool]) -> list[dict[str, object]]:
    return [{"text": text, "final": final} for text, final in shown]


BROWSER_FRAMES: dict[str, tuple[Hear, list[dict[str, object]]]] = {
    "deltas-grow-and-completed-replaces": (
        after_the_first_audio(
            a_delta("Gu"),
            a_delta("Ha", item="item-2"),
            a_delta("ten"),
            a_completed("Guten Tag."),
        ),
        _transcripts(
            ("Gu", False), ("Ha", False), ("Guten", False), ("Guten Tag.", True)
        ),
    ),
    "empty-completed-sends-nothing-and-clears-the-item": (
        after_the_first_audio(
            a_delta("Ha", item="item-2"),
            a_completed("", item="item-2"),
            a_delta("Neu", item="item-2"),
            a_completed("Neu.", item="item-2"),
        ),
        _transcripts(("Ha", False), ("Neu", False), ("Neu.", True)),
    ),
    "session-and-turn-events-skipped": (
        after_the_first_audio(
            wire.SessionCreated(
                session=wire.TranscriptionSession(type="transcription")
            ),
            wire.SessionUpdated(
                session=wire.TranscriptionSession(type="transcription")
            ),
            wire.SpeechStarted(audio_start_ms=0, item_id=AN_ITEM),
            wire.SpeechStopped(audio_end_ms=1, item_id=AN_ITEM),
            wire.AudioCommitted(item_id=AN_ITEM, previous_item_id=None),
            wire.ItemCreated(
                previous_item_id=None,
                item=wire.UserMessageItem(id=AN_ITEM),
            ),
            a_completed("Ein Wort."),
        ),
        _transcripts(("Ein Wort.", True)),
    ),
    "failed-then-error": (
        after_the_first_audio(a_failed_item(), an_error_event()),
        [],
    ),
    "error-alone": (after_the_first_audio(an_error_event()), []),
    **{
        f"closes-{code}": (after_the_first_audio(close_code=code), [])
        for code in (1000, 1009, 1011, 1013)
    },
    "peer-crashes-mid-session": (crashing_after_the_first_audio(), []),
    "unknown-event": (
        after_the_first_audio(json.dumps({"type": "unknown.event", "event_id": "e"})),
        [],
    ),
    "invalid-payload": (
        after_the_first_audio(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.delta",
                    "event_id": "e",
                    "item_id": AN_ITEM,
                    "delta": ["not", "text"],
                }
            )
        ),
        [],
    ),
    "non-json-frame": (after_the_first_audio("not json at all"), []),
    "busy-at-the-upgrade": (busy_at_the_upgrade(), []),
    "refused-401": (denying(401), []),
    "refused-403": (denying(403), []),
    "refused-503": (denying(503), []),
}
