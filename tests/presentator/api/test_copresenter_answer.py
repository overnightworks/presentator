"""The co-presenter answers through the gateway, from sign-in to the voice.

Every test asks as a signed-in admin of the talk host over real identity tables
and a fake issuer: the real groups reader, the real answerer and its backend over
the client's fake gateway or a held or refusing transport, the real sentence
splitter and a fake voice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Final

import pytest
from agent_runtime_client.backend.fake_gateway import FakeGateway, Reply, completion
from webauth.cookies import DEFAULT_SESSION_COOKIE_NAME

from presentator.adapters.deck_texts import MirroredDeckTexts
from presentator.adapters.sentences import split_sentences
from presentator.agent.backend import SessionGroups, gateway_answerer
from presentator.api.copresenter import (
    NOT_ENABLED_REASON,
    CoPresenterSurface,
)
from presentator.application.copresenter import (
    AskLimiter,
    AskTicket,
    CoPresenterUse,
    RetryAfter,
)
from presentator.contracts.decks import Build, Deck, Source
from tests.presentator.agent.support.transports import HeldTransport, RefusingTransport
from tests.presentator.application.support.fakes import (
    FakeDeckStore,
    FakeSourceStore,
    FrozenClock,
)
from tests.support.copresenter_support import (
    A_DECK_TEXT,
    A_SOURCE,
    ASK,
    SPOKEN_QUESTION,
    FakeDeckTexts,
    PrivateFakes,
)
from tests.support.deck_source_support import a_source, folders_under, mirrors_under
from tests.support.lobby import (
    A_BROWSERS_HEADERS,
    A_GATEWAY_KEY,
    A_TALK_ADDRESS,
    AN_API_GROUP,
    NOW,
    an_agent_config,
)
from tests.support.talk_support import A_DECK, B_DECK, A, Hosts, two_hosts

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
        AccessRefusal,
        AgentAnswers,
        AskingPerson,
        DeckTexts,
        PersonAccess,
    )
    from tests.support.git_remote import GitRemote
from tests.support.leak_support import everything_shown

type Gateway = FakeGateway | HeldTransport | RefusingTransport

ACCESS: Final = "person-access-MARKER"
REFRESH: Final = "refresh-MARKER"
ROTATED: Final = "rotated-MARKER"
REPLY_BODY: Final = "reply-body-MARKER"
ADMIN_GROUPS: Final = ["/apps/presentator", "/apps/presentator/admin"]
API_MEMBER_GROUPS: Final = [*ADMIN_GROUPS, AN_API_GROUP]
THE_ASKED_DECK: Final = {**SPOKEN_QUESTION, "deck": A_DECK}
FIRST_AUDIO: Final = "presentator.application.copresenter"
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
    ) -> AsyncGenerator[AsyncIterator[str]]:
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
    limiter = RecordingLimiter(clock=FrozenClock(instant=NOW))
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
                person_access=WitnessedAccess(
                    SessionGroups(auth.relying_party, sessions, auth.config), errors
                ),
            ),
            limiter=limiter,
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
        response = ask(talk, scenario.body, scenario.headers)
    finally:
        root.removeHandler(logs)
        root.setLevel(level)
    return response, logs.records


def ask(
    talk: TestClient,
    body: Mapping[str, object] = THE_ASKED_DECK,
    headers: Mapping[str, str] | None = None,
) -> Response:
    return talk.post(ASK, json=body, headers=headers)


A_PUSH: Final = datetime(2026, 1, 15, 9, tzinfo=UTC)
ASKED_MARKER: Final = "asked-deck-MARKER"
OTHER_MARKER: Final = "other-deck-MARKER"
LATER_MARKER: Final = "later-commit-MARKER"
THE_ASKED: Final = "kern"


def the_asked_decks_words_at_its_built_commit(
    tmp_path: Path, remote: GitRemote, caplog: pytest.LogCaptureFixture
) -> Run:
    """A repository of two decks, the asked one built before it was edited."""
    remote.commit(
        {
            f"{THE_ASKED}/slides.md": f"# Eins\n{ASKED_MARKER}\n",
            "anderes/slides.md": f"# Fremd\n{OTHER_MARKER}\n",
        },
        at=A_PUSH,
    )
    built_at = remote.head
    remote.commit(
        {
            f"{THE_ASKED}/slides.md": (
                f"# Eins\n{ASKED_MARKER}\n\n---\n\n{LATER_MARKER}\n"
            )
        },
        at=A_PUSH,
    )
    source = replace(a_source(remote.url), id=A_SOURCE.id, owner_id=A.id)
    folders_under(tmp_path).folders(source)

    def own_the_built_deck(hosts: Hosts) -> None:
        hosts.store.put(
            Deck(
                slug=THE_ASKED,
                title="Kern",
                changed_at=NOW,
                owner_id=A.id,
                source_id=source.id,
                commit=built_at,
                build=None,
                attempt=None,
            )
        )
        hosts.store.put_build(
            THE_ASKED,
            Build(
                directory=tmp_path / "talk",
                pdf=tmp_path / "deck.pdf",
                commit=built_at,
                built_at=NOW,
            ),
        )

    caplog.set_level(logging.INFO, logger=FIRST_AUDIO)
    scenario = Scenario(
        texts=MirroredDeckTexts(mirrors=mirrors_under(tmp_path)),
        sources=[source],
        arrange=own_the_built_deck,
        body={**SPOKEN_QUESTION, "deck": THE_ASKED},
    )
    return drive(tmp_path, scenario)


def test_a_signed_in_admins_ask_reaches_the_gateway_with_only_that_decks_words(
    tmp_path: Path, remote: GitRemote, caplog: pytest.LogCaptureFixture
) -> None:
    """The deck's words at its built commit, the key as bearer, one text's events."""
    run = the_asked_decks_words_at_its_built_commit(tmp_path, remote, caplog)

    (request,) = run.gateway.requests
    sent = json.dumps(json.loads(request.content)["messages"])
    assert request.headers["authorization"] == f"Bearer {A_GATEWAY_KEY}"
    assert ASKED_MARKER in sent
    assert OTHER_MARKER not in sent
    assert LATER_MARKER not in sent
    assert run.response.status_code == HTTPStatus.OK
    assert run.events == ["text", "sentence", "audio", "sentence", "audio", "done"]
    (line,) = [r.getMessage() for r in caplog.records if r.name == FIRST_AUDIO]
    assert re.fullmatch(r"co-presenter first audio: ms=\d+", line)
    assert the_ticket_was_released(run)


def the_ticket_was_released(run: Run) -> bool:
    """Whether every ticket the ask took is free again, so its session may ask."""
    assert run.limiter.session_ids
    for session_id in set(run.limiter.session_ids):
        ticket = run.limiter.admit(session_id)
        if not isinstance(ticket, AskTicket):
            return False
        ticket.release()
    return True


@pytest.mark.parametrize(
    ("setup", "status", "detail"),
    [
        pytest.param(
            Scenario(refresh_token=None),
            HTTPStatus.UNAUTHORIZED,
            "Sign in again",
            id="no-refresh-token",
        ),
        pytest.param(
            Scenario(before_ask=the_refresh_token_is_revoked),
            HTTPStatus.UNAUTHORIZED,
            "Sign in again",
            id="invalid-grant",
        ),
        pytest.param(
            Scenario(before_ask=the_session_ends_during_the_grant),
            HTTPStatus.UNAUTHORIZED,
            "Not authenticated",
            id="session-ended",
        ),
        pytest.param(
            Scenario(before_ask=the_token_endpoint_goes_down),
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Co-presenter unavailable",
            id="issuer-unavailable",
        ),
        pytest.param(
            Scenario(before_ask=the_token_thread_fails),
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Co-presenter unavailable",
            id="groups-thread-fails",
        ),
    ],
)
def test_an_access_refusal_is_answered_before_the_gateway_is_called(
    tmp_path: Path, setup: Scenario, status: HTTPStatus, detail: str
) -> None:
    """C5: the ruled 401 and 503 come before the stream, and the ticket is freed."""
    run = drive(tmp_path, setup)

    assert run.response.status_code == status
    assert run.response.json() == {"detail": detail}
    assert run.gateway.requests == []
    assert run.fakes.speaking.spoken == []
    assert the_ticket_was_released(run)


def test_a_session_that_ended_during_the_grant_asked_the_issuer_exactly_once(
    tmp_path: Path,
) -> None:
    """C5: the grant ran, and nothing was spent after it."""
    run = drive(tmp_path, Scenario(before_ask=the_session_ends_during_the_grant))

    assert run.grants_since_sign_in == ["refresh_token"]


def test_a_doubled_session_cookie_is_refused_before_any_grant(tmp_path: Path) -> None:
    """C5: webauth reads a doubled cookie as no session, so its gate refuses.

    The refusal is the library's 401 before the route runs, as it answers the
    overlay's `fetch`, which sets no `Accept` (a page visit would be sent to
    sign in); the route's own 401 "Not authenticated" for a missing admission
    is pinned in test_copresenter.py.
    """
    run = drive(tmp_path)
    grants = len(run.hosts.issuer.grant_types)
    session = run.talk.cookies[DEFAULT_SESSION_COOKIE_NAME]
    named = f"{DEFAULT_SESSION_COOKIE_NAME}={session}"

    refused = run.talk.post(
        ASK,
        json=THE_ASKED_DECK,
        headers={"cookie": f"{named}; {named}", "accept": "*/*"},
    )

    assert refused.status_code == HTTPStatus.UNAUTHORIZED
    assert refused.json() == {"detail": "Invalid session"}
    assert len(run.gateway.requests) == 1
    assert len(run.hosts.issuer.grant_types) == grants


def test_one_question_makes_one_refresh_grant_and_no_client_credentials_grant(
    tmp_path: Path,
) -> None:
    """C4: the groups come from one refresh grant; no workload grant exists."""
    run = drive(tmp_path)

    assert run.response.status_code == HTTPStatus.OK
    assert run.grants_since_sign_in == ["refresh_token"]


def test_a_person_outside_the_api_group_is_not_enabled_and_no_call_is_made(
    tmp_path: Path,
) -> None:
    """C4: the overlay reads the literal `not-enabled`, so it is spelled out."""
    run = drive(tmp_path, Scenario(groups=ADMIN_GROUPS))

    assert run.events == ["error"]
    assert run.error_payload() == {
        "message": "the answer could not be spoken",
        "reason": "not-enabled",
    }
    assert NOT_ENABLED_REASON == "not-enabled"
    assert run.gateway.requests == []
    assert the_ticket_was_released(run)


def test_a_group_removed_at_the_issuer_between_two_questions_refuses_the_second(
    tmp_path: Path,
) -> None:
    """C4: the groups are read anew for every question."""
    run = drive(tmp_path)
    run.hosts.issuer.hold_groups(ADMIN_GROUPS)

    second = ask(run.talk)

    assert run.events[-1] == "done"
    assert re.findall(r"^event: (\w+)$", second.text, re.MULTILINE) == ["error"]
    assert len(run.gateway.requests) == 1


def test_a_talk_session_that_keeps_no_id_token_asks_and_is_answered(
    tmp_path: Path,
) -> None:
    """C6: the talk host stores no ID token, and the answer comes anyway."""
    run = drive(tmp_path)

    with closing(sqlite3.connect(run.hosts.database)) as connection:
        stored = [row[0] for row in connection.execute("SELECT id_token FROM sessions")]
    assert stored == [None]
    assert run.events[-1] == "done"


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(Reply(429, {"retry-after": "7"}), id="busy"),
        pytest.param(Reply(401), id="unauthorized"),
        pytest.param(Reply(403), id="forbidden"),
        pytest.param(Reply(422), id="budget"),
        pytest.param(Reply(500), id="gateway-down"),
        pytest.param(Reply(200, body=b"not json"), id="malformed"),
        pytest.param(completion(""), id="no-words"),
    ],
)
def test_a_gateway_that_does_not_answer_ends_in_a_plain_error_and_never_in_done(
    tmp_path: Path, reply: Reply
) -> None:
    """C7: no reason reaches the overlay, and no word was spoken."""
    run = drive(tmp_path, Scenario(gateway=lambda: FakeGateway(reply)))

    assert run.events == ["error"]
    assert "reason" not in run.error_payload()
    assert run.fakes.speaking.spoken == []
    assert the_ticket_was_released(run)


def test_a_refused_connection_ends_in_a_plain_error(tmp_path: Path) -> None:
    """C7: the refusing transport."""
    run = drive(tmp_path, Scenario(gateway=RefusingTransport))

    assert run.events == ["error"]
    assert "reason" not in run.error_payload()
    assert len(run.gateway.requests) == 1


def test_speech_failing_on_the_second_sentence_ends_after_the_first_in_error(
    tmp_path: Path,
) -> None:
    """C8: the first sentence was spoken before the failure."""
    scenario = Scenario(
        gateway=lambda: FakeGateway(completion(f"{TWO_SENTENCES} Dritter.")),
        speaking_fails_at=1,
    )

    run = drive(tmp_path, scenario)

    assert run.events == ["text", "sentence", "audio", "sentence", "error"]
    assert "reason" not in run.error_payload()
    assert the_ticket_was_released(run)


@dataclass
class ChangeOnceTheAnswerHangs:
    """A session re-check that changes the world once the gateway holds the request."""

    change: Callable[[Hosts], None]
    waits: int = 0

    async def __call__(self, gateway: Gateway, hosts: Hosts, _seconds: float) -> None:
        assert isinstance(gateway, HeldTransport)
        self.waits += 1
        await asyncio.wait_for(gateway.arrived.wait(), timeout=5)
        self.change(hosts)


def test_an_answer_ends_within_one_recheck_when_the_session_goes(
    tmp_path: Path,
) -> None:
    """C9: no text was sent before the end, and the held call is cancelled."""
    recheck = ChangeOnceTheAnswerHangs(delete_the_sessions)
    scenario = Scenario(gateway=HeldTransport, delay=recheck)

    run = drive(tmp_path, scenario)

    assert isinstance(run.gateway, HeldTransport)
    assert run.response.status_code == HTTPStatus.OK
    assert run.events == []
    assert recheck.waits == 1
    assert run.gateway.cancelled
    assert the_ticket_was_released(run)


@pytest.mark.parametrize(
    "texts",
    [
        pytest.param(FakeDeckTexts(unreadable=True), id="unreadable-mirror"),
        pytest.param(FakeDeckTexts(text=None), id="no-slides"),
    ],
)
def test_a_deck_without_readable_words_ends_in_error_and_never_calls_the_gateway(
    tmp_path: Path, texts: FakeDeckTexts
) -> None:
    """A deck with no words has nothing to ask about."""
    run = drive(tmp_path, Scenario(texts=texts))

    assert run.events == ["error"]
    assert run.gateway.requests == []
    assert the_ticket_was_released(run)


def test_another_admins_deck_gets_the_ghosts_refusal_and_spends_no_grant(
    tmp_path: Path,
) -> None:
    """E11 holds before any grant is made."""
    (tmp_path / "ghost").mkdir()
    (tmp_path / "foreign").mkdir()
    ghost = drive(
        tmp_path / "ghost", Scenario(body={**SPOKEN_QUESTION, "deck": "ghost"})
    )
    foreign = drive(
        tmp_path / "foreign", Scenario(body={**SPOKEN_QUESTION, "deck": B_DECK})
    )

    assert foreign.response.status_code == HTTPStatus.NOT_FOUND
    assert foreign.response.content == ghost.response.content
    assert foreign.grants_since_sign_in == []
    assert foreign.gateway.requests == []


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


@pytest.mark.parametrize("scenario", LEAK_SCENARIOS.values(), ids=LEAK_SCENARIOS)
def test_no_secret_or_word_of_the_person_reaches_a_log_record_or_the_browser(
    tmp_path: Path, scenario: Scenario
) -> None:
    """C10: every log record and error of the ask, and all the browser got."""
    run = drive(tmp_path, scenario)

    logged = everything_shown(run.logged, run.errors)
    browser = f"{run.response.headers}\n{run.response.text}"
    for marker in what_the_ask_must_not_show(run):
        assert marker not in logged
        assert marker not in browser
    assert run.logged


def test_every_generated_answer_event_says_ai_generated_and_error_does_not(
    tmp_path: Path,
) -> None:
    """C8: text, sentence, audio and done say ai_generated; error does not."""
    (tmp_path / "answered").mkdir()
    (tmp_path / "refused").mkdir()
    answered = _payloads_by_event(drive(tmp_path / "answered").response.text)
    refused = _payloads_by_event(
        drive(tmp_path / "refused", Scenario(groups=ADMIN_GROUPS)).response.text
    )

    assert {"text", "sentence", "audio", "done"} <= answered.keys()
    assert all(
        payload["ai_generated"] is True
        for payloads in answered.values()
        for payload in payloads
    )
    assert refused.keys() == {"error"}
    assert "ai_generated" not in refused["error"][0]


def _payloads_by_event(stream: str) -> dict[str, list[dict[str, object]]]:
    by_event: dict[str, list[dict[str, object]]] = {}
    for name, data in re.findall(r"^event: (\w+)\ndata: (.*)$", stream, re.MULTILINE):
        by_event.setdefault(name, []).append(json.loads(data))
    return by_event
