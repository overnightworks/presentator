"""The co-presenter talk host's shared fakes, values and builders."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import threading
from contextlib import AbstractAsyncContextManager, asynccontextmanager, closing
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final

from agents.backend.fake_gateway import FakeGateway, Reply, completion
from speech_client.relay import HopClose

from presentator.adapters.sentences import split_sentences
from presentator.adapters.session_access import SessionAccess
from presentator.agent.backend import gateway_answerer
from presentator.api.copresenter import CoPresenterSurface
from presentator.application.copresenter import (
    AskLimiter,
    AskTicket,
    CoPresenterUse,
    RetryAfter,
)
from presentator.contracts.copresenter import (
    AccessRefusal,
    AskedDeck,
    AskingPerson,
    CoPresenterReadiness,
    CoPresenterUnavailableError,
    DeckText,
    HearingTranscript,
    ShowSlide,
    SlideText,
    SpeechTokens,
)
from presentator.contracts.decks import Source
from presentator.contracts.voice import SampleLanguage
from tests.presentator.agent.support.transports import HeldTransport, RefusingTransport
from tests.presentator.application.support.fakes import (
    FakeDeckStore,
    FakeSourceStore,
    FakeUserStore,
    FrozenClock,
)
from tests.support.deck_page_support import a_build, a_deck_store
from tests.support.deck_text_support import A_DECK_TEXT, FakeDeckTexts
from tests.support.lobby import (
    A_BROWSERS_HEADERS,
    A_GATEWAY_KEY,
    A_SPEECH_SCOPE,
    A_TALK_ADDRESS,
    A_TALK_SOCKET_ADDRESS,
    ADMIN,
    AN_API_GROUP,
    SOURCE_ID,
    Lobby,
    a_browser_at,
    a_crowd,
    a_preferences,
    a_talk_app,
    an_agent_config,
)
from tests.support.talk_support import A_DECK as A_TALKED_DECK
from tests.support.talk_support import A, Hosts, two_hosts

if TYPE_CHECKING:
    from collections.abc import (
        AsyncGenerator,
        AsyncIterator,
        Awaitable,
        Callable,
        Mapping,
    )
    from pathlib import Path

    from fastapi.testclient import TestClient
    from httpx2 import Response

    from presentator.adapters.identity import SqliteSessionRecordStore
    from presentator.api.auth import InstalledAuth
    from presentator.ports.copresenter import (
        AgentAnswers,
        DeckTexts,
        HearingSocket,
        PersonAccess,
    )

TALK_ORIGIN = A_TALK_ADDRESS
A_DECK = "kundenfeedback"
QUESTION = {"said": "Was ist wichtig?", "slide": 2, "language": "de", "deck": A_DECK}
ASK = "/copresenter/ask"
SPOKEN_QUESTION = {"said": "Was ist wichtig?", "slide": 1, "language": "de"}
A_SOURCE = Source(id=SOURCE_ID, name="s", url="u", ref="main", owner_id=ADMIN)


def the_admins_decks() -> FakeDeckStore:
    return a_deck_store(slug=A_DECK, built=a_build())


def a_deck_text_over_the_clients_bound() -> DeckText:
    """The asked deck's words, followed by a slide too large for the client."""
    return replace(
        A_DECK_TEXT,
        slides=(
            *A_DECK_TEXT.slides,
            SlideText(number=3, title="Lang", body="x" * 5_000_000, notes=""),
        ),
    )


ASKED = AskedDeck(deck=the_admins_decks().kept[A_DECK], source=A_SOURCE)


HEARING = f"{A_TALK_SOCKET_ADDRESS}/copresenter/hear?language=de"
POLICY_VIOLATION = 1008
INTERNAL_ERROR = 1011


@dataclass
class RecordingHearing:
    """What a relayed hearing took from the browser and what the service answers."""

    frames: list[bytes] = field(default_factory=list[bytes])
    closed: int = 0
    events: list[HearingTranscript | RuntimeError | None] = field(
        default_factory=lambda: [
            HearingTranscript(text="gehört", final=True, language=SampleLanguage.GERMAN)
        ]
    )


@dataclass
class RecordingSpeech:
    """The speech service's hearing side: readiness and one relayed hearing."""

    readiness_calls: int = 0
    hearing_opens: int = 0
    refusals: int = 0
    relaying: threading.Event = field(default_factory=threading.Event)
    hearing: RecordingHearing = field(default_factory=RecordingHearing)
    readiness_unavailable: bool = False
    local_hearing_ready: bool = True
    heard_with: list[SpeechTokens] = field(default_factory=list[SpeechTokens])

    async def readiness(self) -> CoPresenterReadiness:
        self.readiness_calls += 1
        if self.readiness_unavailable:
            raise CoPresenterUnavailableError
        return CoPresenterReadiness(
            local_hearing_ready=self.local_hearing_ready,
        )

    async def relay(
        self,
        browser: HearingSocket,
        language: str,
        tokens: SpeechTokens,
        *,
        ended: asyncio.Event,
        heard: Callable[[HearingTranscript], Awaitable[None]],
    ) -> None:
        """Take frames, answer each with the next event, and end as the relay does."""
        assert language == "de"
        self.heard_with.append(tokens)
        self.hearing_opens += 1
        self.relaying.set()
        try:
            while await self._answered_one_frame(browser, ended, heard):
                pass
        finally:
            self.hearing.closed += 1

    async def _answered_one_frame(
        self,
        browser: HearingSocket,
        ended: asyncio.Event,
        heard: Callable[[HearingTranscript], Awaitable[None]],
    ) -> bool:
        message = asyncio.ensure_future(browser.receive())
        stop = asyncio.ensure_future(ended.wait())
        try:
            await asyncio.wait({message, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for pending in (message, stop):
                pending.cancel()
            await asyncio.gather(message, stop, return_exceptions=True)
        if stop.done() and not stop.cancelled():
            await browser.close(HopClose.NORMAL)
            return False
        received = message.result()
        if received["type"] == "websocket.disconnect":
            return False
        frame = received.get("bytes")
        if not isinstance(frame, bytes):
            await browser.close(HopClose.TEXT_FRAME)
            return False
        self.hearing.frames.append(frame)
        return await self._reply(browser, heard)

    async def _reply(
        self,
        browser: HearingSocket,
        heard: Callable[[HearingTranscript], Awaitable[None]],
    ) -> bool:
        if not self.hearing.events:
            return True
        event = self.hearing.events.pop(0)
        if isinstance(event, RuntimeError):
            raise event
        if event is None:
            await browser.close(HopClose.SERVICE_FAILED)
            return False
        await heard(event)
        return True

    async def refuse(self, browser: HearingSocket) -> None:
        self.refusals += 1
        await browser.close(HopClose.SERVICE_FAILED)


@dataclass
class RecordingAnswers:
    """The gateway's text, behind the real application use case."""

    messages: list[str] = field(default_factory=list[str])
    persons: list[AskingPerson] = field(default_factory=list[AskingPerson])
    exits: int = 0
    hold: bool = False
    unavailable: BaseException | None = None
    deltas: list[str] = field(default_factory=lambda: ["Eine Antwort."])
    busy_events: int | None = None
    event_hook: Callable[[int], None] | None = None
    generated_events: int = 0
    reader_closed: bool = False
    reader_closed_before_context_exit: bool = False

    def answer(
        self, *, message: str, person: AskingPerson
    ) -> AbstractAsyncContextManager[AsyncIterator[str]]:
        @asynccontextmanager
        async def operation() -> AsyncGenerator[AsyncIterator[str]]:
            self.messages.append(message)
            self.persons.append(person)
            if self.unavailable is not None:
                raise self.unavailable

            async def texts() -> AsyncIterator[str]:
                try:
                    if self.busy_events is not None:
                        for index in range(self.busy_events):
                            if self.event_hook is not None:
                                self.event_hook(index)
                            self.generated_events += 1
                            yield f"Antwort {index} "
                            await asyncio.sleep(0)
                        return
                    for delta in self.deltas:
                        yield delta
                        if self.hold:
                            await asyncio.Future()
                finally:
                    self.reader_closed = True

            try:
                yield texts()
            finally:
                self.reader_closed_before_context_exit = self.reader_closed
                self.exits += 1

        return operation()


@dataclass
class FakeSpeaking:
    """Speaks every sentence as the same WAV header, and remembers what it spoke."""

    spoken: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    fails_at: int | None = None
    spoken_with: list[SpeechTokens] = field(default_factory=list[SpeechTokens])

    async def speak(self, text: str, language: str, tokens: SpeechTokens) -> bytes:
        self.spoken_with.append(tokens)
        if self.fails_at == len(self.spoken):
            raise CoPresenterUnavailableError
        self.spoken.append((text, language))
        return b"RIFF"


@dataclass
class FakePersonAccess:
    """Gives every session the groups of the API group, or refuses it."""

    refusal: AccessRefusal | None = None
    unavailable: bool = False
    sessions: list[str] = field(default_factory=list[str])

    async def groups_of(self, session_id: str) -> frozenset[str] | AccessRefusal:
        self.sessions.append(session_id)
        if self.unavailable:
            raise CoPresenterUnavailableError
        return self.refusal or frozenset({AN_API_GROUP})

    async def speech_tokens_of(self, session_id: str) -> SpeechTokens | AccessRefusal:
        self.sessions.append(session_id)
        if self.unavailable:
            raise CoPresenterUnavailableError
        return self.refusal or SpeechTokens(person="a-person", workload="a-workload")


@dataclass(kw_only=True)
class PrivateFakes:
    """One fake for each port the use case reaches through."""

    answers: RecordingAnswers = field(default_factory=RecordingAnswers)
    speech: RecordingSpeech = field(default_factory=RecordingSpeech)
    speaking: FakeSpeaking = field(default_factory=FakeSpeaking)
    person_access: FakePersonAccess = field(default_factory=FakePersonAccess)
    texts: FakeDeckTexts = field(default_factory=FakeDeckTexts)


def a_use(fakes: PrivateFakes, decks: FakeDeckStore) -> CoPresenterUse:
    sources = FakeSourceStore(sources=[A_SOURCE])
    return CoPresenterUse(
        answers=fakes.answers,
        speech=fakes.speech,
        decks=decks,
        sources=sources,
        texts=fakes.texts,
        boundaries=split_sentences,
        speaking=fakes.speaking,
        person_access=fakes.person_access,
    )


def a_copresenter_talk_host(
    *,
    users: FakeUserStore | None = None,
    delay: Callable[[float], Awaitable[None]] = asyncio.sleep,
    limiter: AskLimiter | None = None,
    decks: FakeDeckStore | None = None,
) -> tuple[Lobby, PrivateFakes, FrozenClock]:
    private = PrivateFakes()
    people = a_crowd(users)
    app = a_talk_app(
        people=people,
        copresenter=CoPresenterSurface(
            use=a_use(private, decks or the_admins_decks()),
            limiter=limiter or AskLimiter(),
            preferences=a_preferences(),
            browser_origin=TALK_ORIGIN,
            delay=delay,
        ),
    )
    browser = a_browser_at(
        app,
        people=people,
        base_url=A_TALK_ADDRESS,
        headers=A_BROWSERS_HEADERS,
    )
    return browser, private, people.clock


def ask(client: TestClient, body: Mapping[str, object] = QUESTION) -> Response:
    return client.post("/copresenter/ask", json=body)


def who(
    client: TestClient,
    deck: str | None = A_DECK,
    *,
    headers: Mapping[str, str] | None = None,
) -> Response:
    params = {} if deck is None else {"deck": deck}
    return client.get("/copresenter/who", params=params, headers=headers)


@dataclass
class ManualDeadline:
    """A deadline released from the test without sleeping or resetting it."""

    started: threading.Event = field(default_factory=threading.Event)
    _checks: list[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = field(
        default_factory=list[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]]
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)

    async def wait(self, _seconds: float) -> None:
        loop = asyncio.get_running_loop()
        check = loop.create_future()
        with self._lock:
            self._checks.append((loop, check))
            self.started.set()
        await asyncio.shield(check)

    def release_first(self) -> None:
        with self._lock:
            loop, check = self._checks[0]
        loop.call_soon_threadsafe(_complete, check)


def _complete(check: asyncio.Future[None]) -> None:
    if not check.done():
        check.set_result(None)


type Gateway = FakeGateway | HeldTransport | RefusingTransport

ACCESS: Final = "person-access-MARKER"
REFRESH: Final = "refresh-MARKER"
ROTATED: Final = "rotated-MARKER"
REPLY_BODY: Final = "reply-body-MARKER"
ADMIN_GROUPS: Final = ["/apps/presentator", "/apps/presentator/admin"]
API_MEMBER_GROUPS: Final = [*ADMIN_GROUPS, AN_API_GROUP]
THE_ASKED_DECK: Final = {**SPOKEN_QUESTION, "deck": A_TALKED_DECK}
TWO_SENTENCES: Final = "Erster Satz. Zweiter Satz."


def a_good_gateway() -> Gateway:
    return FakeGateway(completion("Ein Satz. Noch einer."))


@dataclass
class RecordingLimiter(AskLimiter):
    """The real limiter, remembering which sessions asked it for a ticket."""

    session_ids: list[str] = field(default_factory=list[str])

    def admit(self, session_id: str) -> AskTicket | RetryAfter:
        self.session_ids.append(session_id)
        return super().admit(session_id)


class RecordingLogs(logging.Handler):
    """Every record any logger raises while an ask runs."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        super().__init__(logging.NOTSET)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class WitnessedAnswers:
    """The real answerer, keeping every error it lets out for the leak check."""

    def __init__(self, answers: AgentAnswers, errors: list[BaseException]) -> None:
        """Wrap these answers."""
        self._answers = answers
        self._errors = errors

    @asynccontextmanager
    async def answer(
        self, *, message: str, person: AskingPerson
    ) -> AsyncGenerator[AsyncIterator[str | ShowSlide]]:
        try:
            async with self._answers.answer(message=message, person=person) as events:
                yield events
        except BaseException as error:
            self._errors.append(error)
            raise


class WitnessedAccess:
    """The real group read, keeping every error it lets out for the leak check."""

    def __init__(self, access: PersonAccess, errors: list[BaseException]) -> None:
        """Wrap this access."""
        self._access = access
        self._errors = errors

    async def groups_of(self, session_id: str) -> frozenset[str] | AccessRefusal:
        try:
            return await self._access.groups_of(session_id)
        except BaseException as error:
            self._errors.append(error)
            raise

    async def speech_tokens_of(self, session_id: str) -> SpeechTokens | AccessRefusal:
        try:
            return await self._access.speech_tokens_of(session_id)
        except BaseException as error:
            self._errors.append(error)
            raise


@dataclass
class Run:
    """One ask by a signed-in admin, and everything that could have seen it."""

    response: Response
    hosts: Hosts
    gateway: Gateway
    fakes: PrivateFakes
    talk: TestClient
    limiter: RecordingLimiter
    logged: list[logging.LogRecord]
    grants_before: list[str] = field(default_factory=list[str])
    errors: list[BaseException] = field(default_factory=list[BaseException])

    @property
    def events(self) -> list[str]:
        return re.findall(r"^event: (\w+)$", self.response.text, re.MULTILINE)

    @property
    def grants_since_sign_in(self) -> list[str]:
        return self.hosts.issuer.grant_types[len(self.grants_before) :]

    def error_payload(self) -> dict[str, str]:
        (payload,) = re.findall(
            r"^event: error\ndata: (.*)$", self.response.text, re.MULTILINE
        )
        return json.loads(payload)


def delete_the_sessions(hosts: Hosts) -> None:
    with closing(sqlite3.connect(hosts.database)) as connection:
        connection.execute("DELETE FROM sessions")
        connection.commit()


def the_session_ends_during_the_grant(hosts: Hosts) -> None:
    hosts.issuer.during_token_request = lambda: delete_the_sessions(hosts)


def the_token_endpoint_goes_down(hosts: Hosts) -> None:
    hosts.issuer.token_endpoint_is_down = True


def the_token_thread_fails(hosts: Hosts) -> None:
    def fail() -> None:
        raise RuntimeError(ACCESS)

    hosts.issuer.during_token_request = fail


def the_refresh_token_is_revoked(hosts: Hosts) -> None:
    hosts.issuer.revoke_refresh_token(REFRESH)


def nothing_happens(_hosts: Hosts) -> None:
    return None


@dataclass(frozen=True, kw_only=True)
class Scenario:
    """What differs from the happy path: one factor at a time."""

    gateway: Callable[[], Gateway] = a_good_gateway
    refresh_token: str | None = REFRESH
    groups: list[str] = field(default_factory=lambda: list(API_MEMBER_GROUPS))
    before_ask: Callable[[Hosts], None] = nothing_happens
    texts: DeckTexts | None = None
    delay: Callable[[Gateway, Hosts, float], Awaitable[None]] | None = None
    body: Mapping[str, object] = field(default_factory=lambda: dict(THE_ASKED_DECK))
    sources: list[Source] | None = None
    arrange: Callable[[Hosts], None] = nothing_happens
    speaking_fails_at: int | None = None
    headers: Mapping[str, str] = field(default_factory=dict[str, str])


def _access_over(
    auth: InstalledAuth, sessions: SqliteSessionRecordStore
) -> SessionAccess:
    return SessionAccess(
        auth.relying_party,
        sessions,
        auth.config,
        speech_scope=A_SPEECH_SCOPE,
    )


def the_fakes_of(scenario: Scenario) -> PrivateFakes:
    fakes = PrivateFakes()
    fakes.speaking.fails_at = scenario.speaking_fails_at
    return fakes


def arrange_the_issuer(hosts: Hosts, scenario: Scenario) -> None:
    hosts.issuer.issue_person_access_token(ACCESS)
    hosts.issuer.rotate_refresh_token_to(ROTATED)
    if scenario.refresh_token is not None:
        hosts.issuer.issue_refresh_token(scenario.refresh_token)
    scenario.arrange(hosts)


def drive(tmp_path: Path, scenario: Scenario = Scenario()) -> Run:  # noqa: B008
    """Sign an admin in at the talk host, then ask once."""
    gateway = scenario.gateway()
    limiter = RecordingLimiter()
    fakes = the_fakes_of(scenario)
    hosts_made: list[Hosts] = []
    errors: list[BaseException] = []

    async def wait(seconds: float) -> None:
        if scenario.delay is None:
            await asyncio.sleep(seconds)
        else:
            await scenario.delay(gateway, hosts_made[0], seconds)

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
                speech=fakes.speech,
                decks=store,
                sources=FakeSourceStore(sources=scenario.sources or [A_SOURCE]),
                texts=scenario.texts or fakes.texts,
                boundaries=split_sentences,
                speaking=fakes.speaking,
                person_access=WitnessedAccess(_access_over(auth, sessions), errors),
            ),
            limiter=limiter,
            preferences=a_preferences(),
            browser_origin=A_TALK_ADDRESS,
            delay=wait,
        )

    hosts = two_hosts(tmp_path, copresenter=surface)
    hosts_made.append(hosts)
    arrange_the_issuer(hosts, scenario)
    talk = hosts.browser(A_TALK_ADDRESS, headers=A_BROWSERS_HEADERS)
    hosts.sign_in(talk, A_TALK_ADDRESS, subject=A.id, groups=scenario.groups)
    grants_before = list(hosts.issuer.grant_types)
    scenario.before_ask(hosts)
    response, logged = ask_recording_logs(talk, scenario)
    return Run(
        response,
        hosts,
        gateway,
        fakes,
        talk,
        limiter,
        logged,
        grants_before,
        errors,
    )


def ask_recording_logs(
    talk: TestClient, scenario: Scenario
) -> tuple[Response, list[logging.LogRecord]]:
    """One ask with every logger at DEBUG; the response and each record raised."""
    logs = RecordingLogs()
    root = logging.getLogger()
    level = root.level
    root.addHandler(logs)
    root.setLevel(logging.DEBUG)
    try:
        response = ask_as_asked(talk, scenario.body, scenario.headers)
    finally:
        root.removeHandler(logs)
        root.setLevel(level)
    return response, logs.records


def ask_as_asked(
    talk: TestClient,
    body: Mapping[str, object] = THE_ASKED_DECK,
    headers: Mapping[str, str] | None = None,
) -> Response:
    return talk.post(ASK, json=body, headers=headers)


def _echoing_the_key() -> Gateway:
    return FakeGateway(
        Reply(
            401,
            {"content-type": "application/json"},
            json.dumps({"detail": f"bad {A_GATEWAY_KEY} {REPLY_BODY}"}).encode(),
        )
    )


LEAK_SCENARIOS: Final = {
    "happy": Scenario(),
    "sign-in-again": Scenario(refresh_token=None),
    "invalid-grant": Scenario(before_ask=the_refresh_token_is_revoked),
    "session-ended": Scenario(before_ask=the_session_ends_during_the_grant),
    "issuer-unavailable": Scenario(before_ask=the_token_endpoint_goes_down),
    "groups-thread-fails": Scenario(before_ask=the_token_thread_fails),
    "not-enabled": Scenario(groups=ADMIN_GROUPS),
    "busy": Scenario(gateway=lambda: FakeGateway(Reply(429, {"retry-after": "7"}))),
    "gateway-echoes-the-key": Scenario(gateway=_echoing_the_key),
    "gateway-forbids": Scenario(gateway=lambda: FakeGateway(Reply(403))),
    "gateway-rejects-the-request": Scenario(
        gateway=lambda: FakeGateway(
            Reply(422, {}, json.dumps({"detail": f"{A_GATEWAY_KEY}"}).encode())
        )
    ),
    "busy-without-a-wait": Scenario(gateway=lambda: FakeGateway(Reply(429))),
    "gateway-down": Scenario(gateway=lambda: FakeGateway(Reply(500))),
    "malformed-reply": Scenario(
        gateway=lambda: FakeGateway(Reply(body=f"{{{REPLY_BODY}".encode()))
    ),
    "foreign-origin": Scenario(headers={"origin": "https://elsewhere.example"}),
    "question-too-long": Scenario(body={**THE_ASKED_DECK, "said": "x" * 2_001}),
    "deck-name-too-long": Scenario(body={**THE_ASKED_DECK, "deck": "d" * 5_000}),
    "deck-content-over-the-clients-bound": Scenario(
        texts=FakeDeckTexts(text=a_deck_text_over_the_clients_bound())
    ),
    "no-words": Scenario(gateway=lambda: FakeGateway(completion(""))),
    "refused-connection": Scenario(gateway=RefusingTransport),
    "speech-fails-on-the-second-sentence": Scenario(
        gateway=lambda: FakeGateway(completion(f"{TWO_SENTENCES} Dritter.")),
        speaking_fails_at=1,
    ),
}


def what_the_ask_must_not_show(run: Run) -> tuple[str, ...]:
    """Every secret and every word of the person, as the instance holds them."""
    return (
        A_GATEWAY_KEY,
        REFRESH,
        ROTATED,
        ACCESS,
        *run.hosts.issuer.issued_id_tokens,
        A.id,
        *accounts_of(run.hosts),
        *API_MEMBER_GROUPS,
        str(SPOKEN_QUESTION["said"]),
        *A_DECK_WORDS,
        REPLY_BODY,
    )


A_DECK_WORDS: Final = tuple(
    word for slide in A_DECK_TEXT.slides for word in (slide.body, slide.notes) if word
)


def accounts_of(hosts: Hosts) -> list[str]:
    with closing(sqlite3.connect(hosts.database)) as connection:
        return [row[0] for row in connection.execute("SELECT id FROM users")]
