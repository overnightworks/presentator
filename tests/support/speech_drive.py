"""The co-presenter talk host with its real speech adapters against the fake service."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from presentator.adapters.sentences import split_sentences
from presentator.adapters.session_access import SessionAccess
from presentator.adapters.speech import SpeechHearing, SpeechSpeaking
from presentator.agent.backend import gateway_answerer
from presentator.api.copresenter import CoPresenterSurface
from presentator.application.copresenter import CoPresenterUse
from tests.presentator.application.support.fakes import FakeSourceStore, FrozenClock
from tests.support.copresenter_support import (
    A_SOURCE,
    ACCESS,
    API_MEMBER_GROUPS,
    REFRESH,
    ROTATED,
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
)
from tests.support.deck_text_support import FakeDeckTexts
from tests.support.lobby import (
    A_BROWSERS_HEADERS,
    A_SPEECH_SCOPE,
    A_SPEECH_VOICE,
    A_TALK_ADDRESS,
    NOW,
    an_agent_config,
)
from tests.support.speech_service import FakeSpeechService, a_served_app
from tests.support.talk_support import A, Hosts, two_hosts

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping
    from pathlib import Path

    from fastapi.testclient import TestClient
    from httpx2 import Response

    from presentator.adapters.identity import SqliteSessionRecordStore
    from presentator.api.auth import InstalledAuth
    from tests.presentator.application.support.fakes import FakeDeckStore

_WIDE_OPEN_LOGGERS = ("httpx2", "httpcore2", "websockets", "wsproto")
WORKLOAD = "workload-token-MARKER"


@dataclass(frozen=True, kw_only=True)
class SpeechScenario:
    """What differs from a good instance: one factor at a time."""

    gateway: Callable[[], Gateway] = a_good_gateway
    service: FakeSpeechService = field(default_factory=FakeSpeechService)
    refresh_token: str | None = REFRESH
    arrange: Callable[[Hosts], None] = nothing_happens


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


def a_surface_over(
    origin: str,
    *,
    gateway: Gateway,
    limiter: RecordingLimiter,
    deadline: ManualDeadline,
    errors: list[BaseException],
) -> Callable[
    [FakeDeckStore, SqliteSessionRecordStore, InstalledAuth], CoPresenterSurface
]:
    """The talk host's surface over the real speech adapters and session access."""

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
                speech=SpeechHearing(origin, voice=A_SPEECH_VOICE),
                decks=store,
                sources=FakeSourceStore(sources=[A_SOURCE]),
                texts=FakeDeckTexts(),
                boundaries=split_sentences,
                speaking=SpeechSpeaking(origin, voice=A_SPEECH_VOICE),
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
    tmp_path: Path,
    scenario: SpeechScenario = SpeechScenario(),  # noqa: B008
) -> Generator[SpeechInstance]:
    """Sign an admin in at the talk host, over the fake service, for the block."""
    limiter = RecordingLimiter(clock=FrozenClock(instant=NOW))
    deadline = ManualDeadline()
    gateway = scenario.gateway()
    errors: list[BaseException] = []
    with a_served_app(scenario.service.app()) as origin:
        hosts = two_hosts(
            tmp_path,
            copresenter=a_surface_over(
                origin,
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
        scenario.arrange(hosts)
        talk = hosts.browser(A_TALK_ADDRESS, headers=A_BROWSERS_HEADERS)
        hosts.sign_in(talk, A_TALK_ADDRESS, subject=A.id, groups=API_MEMBER_GROUPS)
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


def session_columns(hosts: Hosts) -> dict[str, tuple[object, ...]]:
    """Every stored session row by column name, for the columns that keep no token."""
    with closing(sqlite3.connect(hosts.database)) as connection:
        rows = connection.execute("SELECT * FROM sessions")
        names = [column[0] for column in rows.description]
        columns: dict[str, tuple[object, ...]] = {
            name: tuple(row[index] for row in rows.fetchall())
            for index, name in enumerate(names)
            if "token" not in name and name != "user_id"
        }
    return columns
