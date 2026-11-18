"""The co-presenter answers through the gateway, from sign-in to the voice.

Every test asks as a signed-in admin of the talk host over real identity tables
and a fake issuer: the real groups reader, the real answerer and its backend over
the client's fake gateway or a held or refusing transport, the real sentence
splitter and a fake voice.
"""

from __future__ import annotations

import asyncio
import json
import re
from concurrent.futures import CancelledError
from dataclasses import dataclass, replace
from http import HTTPStatus
from typing import TYPE_CHECKING, Final

import pytest
from agents.backend.fake_gateway import (
    FakeGateway,
    Reply,
    completion,
    tool_call,
    tool_round,
)
from sqlalchemy import Engine, text
from webauth.cookies import DEFAULT_SESSION_COOKIE_NAME

from presentator.adapters.build_cache import BuildCache
from presentator.adapters.deck_texts import BuiltDeckTexts
from presentator.contracts.copresenter import RetryAfter
from presentator.contracts.decks import Build, BuildRun, Deck
from tests.presentator.agent.support.transports import HeldTransport, RefusingTransport
from tests.support.copresenter_support import (
    A_SOURCE,
    ADMIN_GROUPS,
    ASK,
    LEAK_SCENARIOS,
    SPOKEN_QUESTION,
    THE_ASKED_DECK,
    TWO_SENTENCES,
    Gateway,
    RecordingLimiter,
    Run,
    Scenario,
    a_copresenter_talk_host,
    ask,
    ask_as_asked,
    delete_the_sessions,
    drive,
    the_refresh_token_is_revoked,
    the_session_ends_during_the_grant,
    the_token_endpoint_goes_down,
    the_token_thread_fails,
    what_the_ask_must_not_show,
)
from tests.support.deck_source_support import a_source
from tests.support.deck_text_support import FakeDeckTexts
from tests.support.lobby import (
    A_GATEWAY_KEY,
    NOW,
)
from tests.support.talk_support import B_DECK, A, Hosts

if TYPE_CHECKING:
    from collections.abc import (
        Callable,
    )
    from pathlib import Path

from tests.support.leak_support import everything_shown
from tests.support.log_lines import json_logged
from tests.support.run_objects import A_CACHE_BOUND, DirectoryRunObjects

ASKED_MARKER: Final = "asked-deck-MARKER"
OTHER_MARKER: Final = "other-deck-MARKER"
LATER_MARKER: Final = "later-commit-MARKER"
THE_ASKED: Final = "kern"


def write_two_runs(builds: Path, *, asked: str) -> None:
    """The asked deck's run, and a run with another deck's and a later push's words.

    The run holds the deck's tree as its build exported it; what the source
    carries beside it or after it is in no run of this deck.
    """
    run = builds / asked
    (run / "out").mkdir(parents=True)
    (run / "deck").mkdir()
    (run / "deck" / "slides.md").write_text(
        f"# Eins\n{ASKED_MARKER}\n", encoding="utf-8"
    )
    other = builds / "another-run" / "deck"
    other.mkdir(parents=True)
    (other / "slides.md").write_text(
        f"# Fremd\n{OTHER_MARKER}\n{LATER_MARKER}\n", encoding="utf-8"
    )


def the_asked_decks_words_at_its_built_commit(engine: Engine, tmp_path: Path) -> Run:
    """An ask about a deck whose build left its run beside another run."""
    built_at = "a-built-commit"
    write_two_runs(tmp_path / "builds", asked=built_at)
    source = replace(
        a_source("git@heimserver:decks.git"), id=A_SOURCE.id, owner_id=A.id
    )

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
                run=BuildRun(built_at),
                directory=tmp_path / "talk",
                pdf=tmp_path / "deck.pdf",
                commit=built_at,
                built_at=NOW,
                toolchain=None,
            ),
        )

    scenario = Scenario(
        texts=BuiltDeckTexts(
            cache=BuildCache(
                root=tmp_path / "builds",
                store=DirectoryRunObjects(tmp_path / "store"),
                bound_bytes=A_CACHE_BOUND,
            )
        ),
        sources=[source],
        arrange=own_the_built_deck,
        body={**SPOKEN_QUESTION, "deck": THE_ASKED},
    )
    return drive(engine, scenario)


def test_a_signed_in_admins_ask_reaches_the_gateway_with_only_that_decks_words(
    engine: Engine,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CP1: the deck's words at its built commit, the key as bearer, one text."""
    with json_logged(capsys) as logged:
        run = the_asked_decks_words_at_its_built_commit(engine, tmp_path)

    (request,) = run.gateway.requests
    sent = json.dumps(json.loads(request.content)["messages"])
    assert request.headers["authorization"] == f"Bearer {A_GATEWAY_KEY}"
    assert ASKED_MARKER in sent
    assert OTHER_MARKER not in sent
    assert LATER_MARKER not in sent
    assert run.response.status_code == HTTPStatus.OK
    assert run.events == ["text", "sentence", "audio", "sentence", "audio", "done"]
    (line,) = [one for one in logged.lines if one["event"] == "answer.ended"]
    assert line["outcome"] == "ok"
    assert isinstance(line["first_output_ms"], float)
    assert the_ticket_was_released(run)


def the_ticket_was_released(run: Run) -> bool:
    """Whether every ticket the ask took is free again, so its session may ask."""
    assert run.limiter.session_ids
    for session_id in set(run.limiter.session_ids):
        ticket = run.limiter.admit(session_id)
        if isinstance(ticket, RetryAfter):
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
    engine: Engine, setup: Scenario, status: HTTPStatus, detail: str
) -> None:
    """CP6: the ruled 401 and 503 come before the stream, and the ticket is freed."""
    run = drive(engine, setup)

    assert run.response.status_code == status
    assert run.response.json() == {"detail": detail}
    assert run.gateway.requests == []
    assert run.fakes.speaking.spoken == []
    assert the_ticket_was_released(run)


def test_a_session_that_ended_during_the_grant_asked_the_issuer_exactly_once(
    engine: Engine,
) -> None:
    """The grant ran, and nothing was spent after it."""
    run = drive(engine, Scenario(before_ask=the_session_ends_during_the_grant))

    assert run.grants_since_sign_in == ["refresh_token"]


def test_a_doubled_session_cookie_is_refused_before_any_grant(engine: Engine) -> None:
    """Webauth reads a doubled cookie as no session, so its gate refuses.

    The refusal is the library's 401 before the route runs, as it answers the
    overlay's `fetch`, which sets no `Accept` (a page visit would be sent to
    sign in); the route's own 401 "Not authenticated" for a missing admission
    is pinned in test_copresenter.py.
    """
    run = drive(engine)
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


def test_a_person_outside_the_api_group_is_not_enabled_and_no_call_is_made(
    engine: Engine,
) -> None:
    """CP3, CP6: the overlay reads the literal `not-enabled`, so it is spelled out."""
    run = drive(engine, Scenario(groups=ADMIN_GROUPS))

    assert run.events == ["error"]
    assert run.error_payload() == {
        "message": "the answer could not be spoken",
        "reason": "not-enabled",
    }
    assert run.gateway.requests == []
    assert the_ticket_was_released(run)


def test_a_group_removed_at_the_issuer_between_two_questions_refuses_the_second(
    engine: Engine,
) -> None:
    """CP3: the groups are read anew for every question."""
    run = drive(engine)
    run.hosts.issuer.hold_groups(ADMIN_GROUPS)

    second = ask_as_asked(run.talk)

    assert run.events[-1] == "done"
    assert re.findall(r"^event: (\w+)$", second.text, re.MULTILINE) == ["error"]
    assert len(run.gateway.requests) == 1


def test_a_talk_session_that_keeps_no_id_token_asks_and_is_answered(
    engine: Engine,
) -> None:
    """The talk host stores no ID token, and the answer comes anyway."""
    run = drive(engine)

    with run.hosts.database.connect() as connection:
        stored = list(
            connection.execute(text("SELECT id_token FROM sessions")).scalars()
        )
    assert stored == [None]
    assert run.events[-1] == "done"


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(Reply(401), id="unauthorized"),
        pytest.param(Reply(403), id="forbidden"),
        pytest.param(Reply(422), id="unprocessable"),
        pytest.param(Reply(500), id="gateway-down"),
        pytest.param(Reply(200, body=b"not json"), id="malformed"),
        pytest.param(completion(""), id="no-words"),
    ],
)
def test_a_gateway_that_does_not_answer_ends_in_a_plain_error_and_never_in_done(
    engine: Engine, reply: Reply
) -> None:
    """No reason reaches the overlay, and no word was spoken."""
    run = drive(engine, Scenario(gateway=lambda: FakeGateway(reply)))

    assert run.events == ["error"]
    assert "reason" not in run.error_payload()
    assert run.fakes.speaking.spoken == []
    assert the_ticket_was_released(run)


def a_gateway_error(error_type: str) -> bytes:
    """A gateway reply body that names its error type."""
    return json.dumps({"error": {"type": error_type, "code": "422"}}).encode()


@pytest.mark.parametrize(
    ("reply", "stop"),
    [
        pytest.param(
            Reply(429, {"retry-after": "7"}),
            {"reason": "busy", "retry_after_s": 7},
            id="busy-with-a-wait",
        ),
        pytest.param(Reply(429), {"reason": "busy"}, id="busy-without-a-wait"),
        pytest.param(
            Reply(422, body=a_gateway_error("budget_exceeded")),
            {"reason": "budget-spent"},
            id="budget-spent",
        ),
    ],
)
def test_a_busy_gateway_or_a_spent_budget_ends_in_its_own_error_reason(
    engine: Engine, reply: Reply, stop: dict[str, object]
) -> None:
    """CP6: the overlay reads these literals, so they are spelled out."""
    run = drive(engine, Scenario(gateway=lambda: FakeGateway(reply)))

    assert run.events == ["error"]
    assert run.error_payload() == {"message": "the answer could not be spoken", **stop}
    assert run.fakes.speaking.spoken == []
    assert the_ticket_was_released(run)


def test_a_refused_connection_ends_in_a_plain_error(engine: Engine) -> None:
    """The refusing transport."""
    run = drive(engine, Scenario(gateway=RefusingTransport))

    assert run.events == ["error"]
    assert "reason" not in run.error_payload()
    assert len(run.gateway.requests) == 1


def test_speech_failing_on_the_second_sentence_ends_after_the_first_in_error(
    engine: Engine,
) -> None:
    """The first sentence was spoken before the failure."""
    scenario = Scenario(
        gateway=lambda: FakeGateway(completion(f"{TWO_SENTENCES} Dritter.")),
        speaking_fails_at=1,
    )

    run = drive(engine, scenario)

    assert run.events == ["text", "sentence", "audio", "sentence", "error"]
    assert "reason" not in run.error_payload()
    assert the_ticket_was_released(run)


def test_the_first_sentence_is_spoken_before_the_last_piece_is_read(
    engine: Engine,
) -> None:
    """An answer streamed in pieces has its first sentence out before its last piece."""
    scenario = Scenario(
        gateway=lambda: FakeGateway(completion("Erster Satz. ", "Zweiter ", "Satz."))
    )

    events = drive(engine, scenario).events

    last_piece = max(at for at, name in enumerate(events) if name == "text")
    assert events.index("sentence") < last_piece


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
    engine: Engine,
) -> None:
    """CP14: no text was sent before the end, and the held call is cancelled."""
    recheck = ChangeOnceTheAnswerHangs(delete_the_sessions)
    scenario = Scenario(gateway=HeldTransport, delay=recheck)

    run = drive(engine, scenario)

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
    engine: Engine, texts: FakeDeckTexts
) -> None:
    """CP1: a deck with no words has nothing to ask about."""
    run = drive(engine, Scenario(texts=texts))

    assert run.events == ["error"]
    assert run.gateway.requests == []
    assert the_ticket_was_released(run)


def test_another_admins_deck_gets_the_ghosts_refusal_and_spends_no_grant(
    engine: Engine,
) -> None:
    """CP2: the ownership refusal comes before any grant is made."""
    ghost = drive(engine, Scenario(body={**SPOKEN_QUESTION, "deck": "ghost"}))
    foreign = drive(engine, Scenario(body={**SPOKEN_QUESTION, "deck": B_DECK}))

    assert foreign.response.status_code == HTTPStatus.NOT_FOUND
    assert foreign.response.content == ghost.response.content
    assert foreign.grants_since_sign_in == []
    assert foreign.gateway.requests == []


@pytest.mark.parametrize("scenario", LEAK_SCENARIOS.values(), ids=LEAK_SCENARIOS)
def test_no_secret_or_word_of_the_person_reaches_a_log_record_or_the_browser(
    engine: Engine, scenario: Scenario
) -> None:
    """Every log record and error of the ask, and all the browser got."""
    run = drive(engine, scenario)

    logged = everything_shown(run.logged, run.errors)
    browser = f"{run.response.headers}\n{run.response.text}"
    for marker in what_the_ask_must_not_show(run):
        assert marker not in logged
        assert marker not in browser
    assert run.logged


def test_every_generated_answer_event_says_ai_generated_and_error_does_not(
    engine: Engine,
) -> None:
    """CP12: text, sentence, audio and done say ai_generated; error does not."""
    answered = _payloads_by_event(drive(engine).response.text)
    refused = _payloads_by_event(
        drive(engine, Scenario(groups=ADMIN_GROUPS)).response.text
    )

    assert {"text", "sentence", "audio", "done"} <= answered.keys()
    assert all(
        payload["ai_generated"] is True
        for payloads in answered.values()
        for payload in payloads
    )
    assert refused.keys() == {"error"}
    assert "ai_generated" not in refused["error"][0]


def test_a_slide_event_carries_the_number_without_the_ai_mark(
    engine: Engine,
) -> None:
    """CP12: the slide event is a control event like error, so it is not marked."""
    scenario = Scenario(
        gateway=lambda: FakeGateway(
            tool_round(tool_call("call-1", "show_slide", {"number": 3})),
            completion("Gut."),
        )
    )

    answered = _payloads_by_event(drive(engine, scenario).response.text)

    assert answered["slide"] == [{"number": 3}]
    assert answered["done"][0]["ai_generated"] is True


def _payloads_by_event(stream: str) -> dict[str, list[dict[str, object]]]:
    by_event: dict[str, list[dict[str, object]]] = {}
    for name, data in re.findall(r"^event: (\w+)\ndata: (.*)$", stream, re.MULTILINE):
        by_event.setdefault(name, []).append(json.loads(data))
    return by_event


@pytest.mark.parametrize(
    ("raised", "surfaced", "outcome", "reason"),
    [
        (asyncio.CancelledError(), CancelledError, "cancelled", None),
        (RuntimeError("broken"), RuntimeError, "failed", "unexpected"),
    ],
    ids=["cancelled", "unexpected"],
)
def test_an_ask_that_breaks_while_it_is_prepared_still_writes_its_answer_line(
    raised: BaseException,
    surfaced: type[BaseException],
    outcome: str,
    reason: str | None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    limiter = RecordingLimiter()
    talk, private, _clock = a_copresenter_talk_host(limiter=limiter)
    private.person_access.raises = raised
    talk.sign_in()

    with json_logged(capsys) as logged, pytest.raises(surfaced):
        ask(talk.client)

    (line,) = [one for one in logged.lines if one["event"] == "answer.ended"]
    assert line["outcome"] == outcome
    assert line.get("reason") == reason
    (session_id,) = set(limiter.session_ids)
    ticket = limiter.admit(session_id)
    assert not isinstance(ticket, RetryAfter)


class _LimiterThatRaises:
    """An ask limiter whose admission breaks with what it is given."""

    def __init__(self, raised: BaseException) -> None:
        self._raised = raised

    def admit(self, session_id: str) -> RetryAfter:
        """Raise, as admission does when the lease store breaks."""
        raise self._raised


@pytest.mark.parametrize(
    ("raised", "surfaced", "outcome", "reason"),
    [
        (asyncio.CancelledError(), CancelledError, "cancelled", None),
        (RuntimeError("broken"), RuntimeError, "failed", "unexpected"),
    ],
    ids=["cancelled", "unexpected"],
)
def test_an_ask_whose_admission_breaks_still_writes_its_answer_line(
    raised: BaseException,
    surfaced: type[BaseException],
    outcome: str,
    reason: str | None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    talk, _private, _clock = a_copresenter_talk_host(limiter=_LimiterThatRaises(raised))
    talk.sign_in()

    with json_logged(capsys) as logged, pytest.raises(surfaced):
        ask(talk.client)

    (line,) = [one for one in logged.lines if one["event"] == "answer.ended"]
    assert line["outcome"] == outcome
    assert line.get("reason") == reason
