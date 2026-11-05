"""An ask and a hearing through the real speech adapters: tokens, refusals, leaks."""

from __future__ import annotations

import json
import logging
from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from agent_runtime_client.backend.fake_gateway import FakeGateway, completion
from fastapi.responses import Response
from speech_runtime_client import wire

from tests.support.copresenter_support import (
    ACCESS,
    ADMIN_GROUPS,
    HEARING,
    ROTATED,
    TALK_ORIGIN,
    THE_GOOD_ANSWER,
    Scenario,
    the_refresh_token_is_revoked,
    the_session_ends_during_the_grant,
    the_ticket_was_released,
    the_token_endpoint_goes_down,
    the_token_thread_fails,
    who,
)
from tests.support.leak_support import recording_every_logger
from tests.support.speech_drive import (
    HEARD_AT_THE_END,
    SAID,
    WORKLOAD,
    drive_through_speech,
    hear_through,
    leaks_of,
    the_ask_beyond_its_answer,
    the_audio_of,
    the_hearing_beyond_its_transcript,
)
from tests.support.speech_service import (
    A_MARKED_WAV,
    A_SPEECH_SCOPE,
    DELTA,
    BreakingTransport,
    FakeSpeech,
    a_refusal,
    completed,
    delta,
    error_event,
)
from tests.support.talk_support import A_DECK

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.support.copresenter_support import Run
    from tests.support.talk_support import Hosts

SECOND_SENTENCE = "Noch einer."
SPEECH_READ = 2  # the second refresh grant of an ask
FOUR_SENTENCES = ("Eins.", "Zwei.", "Drei.", "Vier.")


def test_every_speak_request_carries_the_two_tokens_the_issuer_issued(
    tmp_path: Path,
) -> None:
    """T1: read from both owners, the issuer and the service."""
    fake = FakeSpeech()

    run = drive_through_speech(tmp_path, fake)

    assert run.response.status_code == HTTPStatus.OK
    assert len(fake.speaks) == len(["Ein Satz.", SECOND_SENTENCE])
    for received in fake.speaks:
        assert received.headers["authorization"] == f"Bearer {ACCESS}"
        assert received.headers[wire.WORKLOAD_TOKEN_HEADER.lower()] == WORKLOAD


def test_sentences_are_spoken_in_order_and_the_first_audio_is_logged_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """T3."""
    fake = FakeSpeech()
    caplog.set_level(logging.INFO)

    run = drive_through_speech(tmp_path, fake)

    spoken = [wire.SpeechRequest.model_validate_json(r.body).input for r in fake.speaks]
    assert spoken == ["Ein Satz.", SECOND_SENTENCE]
    assert run.events == ["text", "sentence", "audio", "sentence", "audio", "done"]
    first_audio = [r for r in caplog.records if "first audio" in r.getMessage()]
    assert len(first_audio) == 1
    assert the_ticket_was_released(run)


def test_the_browser_gets_the_wav_unchanged_with_the_ai_mark_in_it(
    tmp_path: Path,
) -> None:
    """T4."""
    run = drive_through_speech(tmp_path, FakeSpeech())

    assert the_audio_of(run) == [A_MARKED_WAV, A_MARKED_WAV]
    assert b"ICMT" in A_MARKED_WAV


@pytest.mark.parametrize("code", list(wire.RefusalCode))
def test_a_speak_refusal_ends_the_answer_in_a_plain_error_after_what_was_spoken(
    tmp_path: Path, code: wire.RefusalCode
) -> None:
    """T5: even `unauthorized` is "No answer", never "Sign in again" (D5)."""
    fake = FakeSpeech(speak_refusal=a_refusal(code, "voice", message=SAID))

    run = drive_through_speech(tmp_path, fake)

    assert run.events == ["text", "sentence", "error"]
    assert "reason" not in run.error_payload()
    assert the_ticket_was_released(run)


def test_the_workload_grant_asks_for_exactly_the_configured_scope(
    tmp_path: Path,
) -> None:
    """G2."""
    run = drive_through_speech(tmp_path, FakeSpeech())

    assert run.hosts.issuer.workload_scopes == [A_SPEECH_SCOPE]


WORKLOAD_REFUSALS = ("workload_is_refused", "workload_scope_is_invalid")


def _the_issuer_refuses_the_workload(refusal: str) -> Callable[[Hosts], None]:
    def refuse(hosts: Hosts) -> None:
        setattr(hosts.issuer, refusal, True)
        hosts.issuer.refusal_description = SAID

    return refuse


@pytest.mark.parametrize("refusal", WORKLOAD_REFUSALS)
def test_an_issuer_that_refuses_the_workload_grant_ends_the_ask_before_the_stream(
    tmp_path: Path, refusal: str, caplog: pytest.LogCaptureFixture
) -> None:
    """G2: the ruled 503 and the closed reason logged; L1 holds the issuer's words."""
    caplog.set_level(logging.DEBUG)

    fake = FakeSpeech()
    run = drive_through_speech(
        tmp_path, fake, Scenario(arrange=_the_issuer_refuses_the_workload(refusal))
    )

    assert run.response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert run.gateway.requests == []
    assert fake.speaks == []
    assert "issuer gave nothing" in caplog.text
    assert the_ticket_was_released(run)


def test_one_ask_makes_two_refresh_grants_and_one_workload_grant_whatever_it_speaks(
    tmp_path: Path,
) -> None:
    """G3."""
    fake = FakeSpeech()
    scenario = Scenario(
        gateway=lambda: FakeGateway(completion(" ".join(FOUR_SENTENCES))),
    )

    run = drive_through_speech(tmp_path, fake, scenario)

    assert len(fake.speaks) == len(FOUR_SENTENCES)
    assert run.grants_since_sign_in == [
        "refresh_token",
        "refresh_token",
        "client_credentials",
    ]


def test_an_ask_refused_at_the_groups_read_makes_no_workload_grant(
    tmp_path: Path,
) -> None:
    """G3."""
    run = drive_through_speech(
        tmp_path, FakeSpeech(), Scenario(before_ask=the_refresh_token_is_revoked)
    )

    assert "client_credentials" not in run.grants_since_sign_in


def _on_the_second_refresh(act: Callable[[Hosts], None]) -> Callable[[Hosts], None]:
    def arm(hosts: Hosts) -> None:
        def during() -> None:
            if hosts.issuer.grant_types.count("refresh_token") == SPEECH_READ:
                act(hosts)

        hosts.issuer.during_token_request = during

    return arm


def _revoke(hosts: Hosts) -> None:
    hosts.issuer.revoke_refresh_token(ROTATED)


def _end_session(hosts: Hosts) -> None:
    the_session_ends_during_the_grant(hosts)
    hosts.issuer.during_token_request()


def _go_down(hosts: Hosts) -> None:
    the_token_endpoint_goes_down(hosts)


def _fail(hosts: Hosts) -> None:
    the_token_thread_fails(hosts)
    hosts.issuer.during_token_request()


@pytest.mark.parametrize(
    ("act", "status", "detail"),
    [
        pytest.param(_revoke, HTTPStatus.UNAUTHORIZED, "Sign in again", id="revoked"),
        pytest.param(
            _end_session, HTTPStatus.UNAUTHORIZED, "Not authenticated", id="ended"
        ),
        pytest.param(
            _go_down,
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Co-presenter unavailable",
            id="issuer-down",
        ),
        pytest.param(
            _fail,
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Co-presenter unavailable",
            id="thread-fails",
        ),
    ],
)
def test_an_access_refusal_on_the_speech_read_is_answered_before_the_stream(
    tmp_path: Path, act: Callable[[Hosts], None], status: HTTPStatus, detail: str
) -> None:
    """G4: the groups read passed; the speech read is refused."""
    fake = FakeSpeech()
    scenario = Scenario(arrange=_on_the_second_refresh(act))

    run = drive_through_speech(tmp_path, fake, scenario)

    assert run.response.status_code == status
    assert run.response.json() == {"detail": detail}
    assert run.gateway.requests == []
    assert fake.speaks == []
    assert the_ticket_was_released(run)


def _heard(
    tmp_path: Path, fake: FakeSpeech, frames_back: int = 1
) -> tuple[list[object], list[logging.LogRecord]]:
    run = drive_through_speech(tmp_path, fake)
    return hear_through(run, b"\x01\x02", frames_back=frames_back)


def test_a_hearing_carries_the_two_tokens_and_gives_the_browser_the_final_text(
    tmp_path: Path,
) -> None:
    """H1, H3: through the real route."""
    fake = FakeSpeech(after_audio=[delta("geh"), completed("gehört")])

    received, _ = _heard(tmp_path, fake, frames_back=2)

    assert received == [
        {"text": "geh", "final": False},
        {"text": "gehört", "final": True},
    ]
    (upgrade,) = fake.upgrades
    assert upgrade.headers["authorization"] == f"Bearer {ACCESS}"
    assert upgrade.headers[wire.WORKLOAD_TOKEN_HEADER.lower()] == WORKLOAD


@pytest.mark.parametrize(
    "fake",
    [
        FakeSpeech(after_audio=[error_event()]),
        FakeSpeech(upgrade_refusal=a_refusal(wire.RefusalCode.BUSY, retry_after="3")),
        FakeSpeech(upgrade_refusal=a_refusal(wire.RefusalCode.UNAUTHORIZED)),
    ],
    ids=["error-event", "busy", "unauthorized"],
)
def test_a_hearing_the_service_will_not_give_shows_the_generic_error_frame(
    tmp_path: Path, fake: FakeSpeech
) -> None:
    """H4, H5."""
    received, _ = _heard(tmp_path, fake)

    assert received == [{"text": "", "final": True, "error": "hearing unavailable"}]


def test_a_second_lease_is_refused_while_the_first_keeps_hearing(
    tmp_path: Path,
) -> None:
    """H9: one hearing session at a time shows as "No hearing" on the second only."""
    fake = FakeSpeech()
    run = drive_through_speech(tmp_path, fake)

    with run.talk.websocket_connect(HEARING, headers={"origin": TALK_ORIGIN}) as first:
        first.send_bytes(b"\x01\x02")
        assert fake.session_open.wait(timeout=5)
        with run.talk.websocket_connect(
            HEARING, headers={"origin": TALK_ORIGIN}
        ) as second:
            second.send_bytes(b"\x01\x02")
            assert second.receive_json() == {
                "text": "",
                "final": True,
                "error": "hearing unavailable",
            }
    assert fake.open_sessions == 0


def _hearing_error_with_a_marker() -> FakeSpeech:
    return FakeSpeech(after_audio=[error_event(SAID)])


class _SpeechWhoseBodyIsCutOff(FakeSpeech):
    def transports(self) -> httpx2.AsyncBaseTransport:
        return BreakingTransport(httpx2.RemoteProtocolError(SAID))


class _SpeechNobodyAnswers(FakeSpeech):
    def transports(self) -> httpx2.AsyncBaseTransport:
        def refuse(request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError(SAID, request=request)

        return httpx2.MockTransport(refuse)


LEAKY_ASKS: dict[str, tuple[Scenario, Callable[[], FakeSpeech]]] = {
    "good-ask": (Scenario(), FakeSpeech),
    "busy-with-a-wait": (
        Scenario(),
        lambda: FakeSpeech(
            speak_refusal=a_refusal(wire.RefusalCode.BUSY, retry_after="7")
        ),
    ),
    "busy-without-a-wait": (
        Scenario(),
        lambda: FakeSpeech(speak_refusal=a_refusal(wire.RefusalCode.BUSY)),
    ),
    **{
        f"refusal-{code.value}-echoing-the-marker": (
            Scenario(),
            lambda code=code: FakeSpeech(
                speak_refusal=a_refusal(code, "voice", message=SAID)
            ),
        )
        for code in wire.RefusalCode
    },
    "not-json-500": (
        Scenario(),
        lambda: FakeSpeech(speak_refusal=Response(f"not json {SAID}", status_code=500)),
    ),
    "empty-body": (
        Scenario(),
        lambda: FakeSpeech(speak_refusal=Response(b"", media_type="audio/wav")),
    ),
    "truncated-body": (Scenario(), _SpeechWhoseBodyIsCutOff),
    "refused-connection": (Scenario(), _SpeechNobodyAnswers),
    **{
        f"issuer-{refusal}": (
            Scenario(arrange=_the_issuer_refuses_the_workload(refusal)),
            FakeSpeech,
        )
        for refusal in WORKLOAD_REFUSALS
    },
    "revoked-refresh-token": (
        Scenario(before_ask=the_refresh_token_is_revoked),
        FakeSpeech,
    ),
    "session-ends-during-the-grant": (
        Scenario(before_ask=the_session_ends_during_the_grant),
        FakeSpeech,
    ),
    "thread-raises-the-token": (
        Scenario(before_ask=the_token_thread_fails),
        FakeSpeech,
    ),
    "issuer-down": (Scenario(before_ask=the_token_endpoint_goes_down), FakeSpeech),
    "not-enabled": (Scenario(groups=ADMIN_GROUPS), FakeSpeech),
}


@pytest.mark.parametrize(("scenario", "service"), LEAKY_ASKS.values(), ids=LEAKY_ASKS)
def test_no_token_or_word_of_the_ask_reaches_a_log_an_error_or_the_browser(
    tmp_path: Path, scenario: Scenario, service: Callable[[], FakeSpeech]
) -> None:
    """L1: every logger at DEBUG, the quietened libraries included."""
    run = drive_through_speech(tmp_path, service(), scenario)

    assert (
        leaks_of(run, records=run.logged, browser=the_ask_beyond_its_answer(run)) == []
    )
    assert run.logged


@pytest.mark.parametrize(
    ("service", "frames_back", "browser_hears_the_word"),
    [
        pytest.param(
            FakeSpeech(after_audio=[delta(SAID), completed(HEARD_AT_THE_END)]),
            2,
            True,
            id="good-hearing",
        ),
        pytest.param(
            _hearing_error_with_a_marker(), 1, False, id="error-with-a-marker"
        ),
        pytest.param(
            FakeSpeech(after_audio=[{"type": DELTA, "delta": SAID}]),
            1,
            False,
            id="malformed-event",
        ),
        pytest.param(
            FakeSpeech(after_audio=[f"not json {SAID}"]), 1, False, id="non-json"
        ),
        pytest.param(
            FakeSpeech(
                upgrade_refusal=a_refusal(
                    wire.RefusalCode.BUSY, retry_after="3", message=SAID
                )
            ),
            1,
            False,
            id="busy-at-the-upgrade",
        ),
    ],
)
def test_no_token_or_heard_word_of_a_hearing_reaches_a_log_an_error_or_a_row(
    tmp_path: Path,
    service: FakeSpeech,
    frames_back: int,
    *,
    browser_hears_the_word: bool,
) -> None:
    """L1: records, errors and stored rows; the heard word only to the browser."""
    run = drive_through_speech(tmp_path, service)

    received, records = hear_through(run, b"\x01\x02", frames_back=frames_back)

    browser = str(received)
    assert (
        leaks_of(
            run, records=records, browser=the_hearing_beyond_its_transcript(received)
        )
        == []
    )
    assert (SAID in browser) is browser_hears_the_word
    assert (HEARD_AT_THE_END in browser) is browser_hears_the_word
    assert records


def test_a_garbage_health_body_with_a_marker_shows_nothing_of_it(
    tmp_path: Path,
) -> None:
    """L1: the readiness read."""
    fake = FakeSpeech(
        health_answer=a_refusal(wire.RefusalCode.UNAVAILABLE, message=SAID)
    )
    run = drive_through_speech(tmp_path, fake)

    with recording_every_logger() as logs:
        answer = who(run.talk, A_DECK)

    browser = f"{answer.headers}\n{answer.text}"
    assert leaks_of(run, records=logs.records, browser=browser) == []
    assert logs.records


@pytest.mark.parametrize(
    ("frame", "token_stays"),
    [
        pytest.param(
            {"text": f"{SAID} {WORKLOAD}", "final": True}, True, id="transcript"
        ),
        pytest.param(
            {"text": f"{SAID} {WORKLOAD}", "final": True, "error": "x"},
            True,
            id="error",
        ),
    ],
)
def test_the_hearing_filter_hides_only_heard_words_in_transcript_frames(
    frame: dict[str, object], *, token_stays: bool
) -> None:
    shown = the_hearing_beyond_its_transcript([frame])

    assert (WORKLOAD in shown) is token_stays
    assert (SAID in shown) is ("error" in frame)


@pytest.mark.parametrize("event", ["text", "done"])
def test_the_ask_filter_hides_only_the_answer_sentences(event: str) -> None:
    payload = json.dumps({"text": f"{THE_GOOD_ANSWER[0]} {WORKLOAD}"})
    run = SimpleNamespace(
        response=SimpleNamespace(text=f"event: {event}\ndata: {payload}", headers={})
    )

    shown = the_ask_beyond_its_answer(cast("Run", run))

    assert WORKLOAD in shown
    assert THE_GOOD_ANSWER[0] not in shown
