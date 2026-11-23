"""The loop route: a heard line wakes the model, which answers by sound frames."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

import pytest
from agents.backend.fake_gateway import Reply, completion
from speech_client import wire
from sqlalchemy import text
from starlette.websockets import WebSocketDisconnect

from presentator.api.copresenter import GENERIC_ANSWER_FAILURE
from presentator.api.health import Stopping
from presentator.contracts.models import Role
from tests.support.copresenter_support import (
    TALK_ORIGIN,
    a_copresenter_talk_host,
    a_gateway_error,
    delete_the_sessions,
)
from tests.support.deck_text_support import A_DECK_TEXT
from tests.support.log_lines import events_of, json_logged
from tests.support.talk_drive import (
    A_LINE,
    SENTENCES,
    TALK,
    TWO_SENTENCES,
    BrokenLimiter,
    HeldByAnotherPod,
    UnavailableLimiter,
    a_talk,
    closed_at_open,
    last_lines_of,
    lines_in,
    requests_of,
    system_text_of,
)
from tests.support.talk_support import A_DECK, B_DECK, A

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from tests.support.talk_support import Hosts

_POLICY_VIOLATION: Final = 1008
_NORMAL_CLOSURE: Final = 1000
_SERVICE_RESTART: Final = 1012
_INTERNAL_ERROR: Final = 1011
_API_LESS_GROUPS: Final = ["/apps/presentator"]
_A_TRACE: Final = "1" * 32
_A_TURN: Final = f"00-{_A_TRACE}-{'2' * 16}-01"


def test_a_heard_final_wakes_one_decision_and_each_sentence_waits_for_the_one_before(
    engine: Engine,
) -> None:
    with a_talk(engine) as talk:
        talk.say(A_LINE)
        heard, first = talk.until("audio")

        assert heard["kind"] == "heard"
        assert (heard["text"], heard["final"], heard["language"]) == (
            A_LINE,
            True,
            "de",
        )
        talk.played(first["id"])
        second = talk.next_sound()
        talk.played(second["id"])
        (request,) = requests_of(talk)
        assert lines_in(request)[-1]["text"] == A_LINE
        assert first["text"] == SENTENCES[0]
        assert second["text"] == SENTENCES[1]


def test_a_silent_reply_sends_no_sound_and_the_next_line_still_wakes_a_decision(
    engine: Engine,
) -> None:
    with a_talk(engine, completion("[silent]"), completion(TWO_SENTENCES)) as talk:
        talk.say(A_LINE)
        talk.say("Noch eine Zeile.")

        frames = talk.until("audio")

        assert [frame["kind"] for frame in frames] == ["heard", "heard", "audio"]


def test_the_model_gets_only_the_opened_decks_words_and_one_tool(
    engine: Engine,
) -> None:
    with a_talk(engine) as talk:
        talk.say(A_LINE)
        talk.next_sound()
        (request,) = requests_of(talk)

        assert A_DECK_TEXT.title in system_text_of(request)
        assert [tool["function"]["name"] for tool in request["tools"]] == ["show_slide"]


@pytest.mark.parametrize("deck", ["no-such-deck", B_DECK])
def test_a_deck_the_person_does_not_own_closes_1008_and_asks_nothing(
    engine: Engine, deck: str
) -> None:
    code, requests = closed_at_open(engine, f"{TALK}?deck={deck}&language=de")

    assert (code, requests) == (_POLICY_VIOLATION, 0)


@pytest.mark.parametrize(
    "query",
    [
        pytest.param(f"deck={A_DECK}&language=de&other=1", id="a key of no one's"),
        pytest.param(f"deck={A_DECK}&deck={A_DECK}", id="a key twice"),
        pytest.param("language=de&language=en", id="the language twice"),
        pytest.param(f"deck={A_DECK}", id="the language missing"),
        pytest.param("language=de", id="the deck missing"),
        pytest.param("", id="neither"),
    ],
)
def test_a_query_without_the_deck_and_the_language_once_each_closes_1008(
    engine: Engine, query: str
) -> None:
    code, _requests = closed_at_open(engine, f"{TALK}?{query}")

    assert code == _POLICY_VIOLATION


def _outside_the_api_group(hosts: Hosts) -> None:
    hosts.issuer.hold_groups(_API_LESS_GROUPS)


def test_a_person_outside_the_api_group_gets_not_enabled_and_the_gateway_no_request(
    engine: Engine,
) -> None:
    with a_talk(engine, arrange=_outside_the_api_group) as talk:
        talk.say(A_LINE)

        (error,) = talk.until("error")[-1:]

        assert error["reason"] == "not-enabled"
        assert requests_of(talk) == []


def test_a_person_who_leaves_the_api_group_mid_talk_is_refused_at_the_next_decision(
    engine: Engine,
) -> None:
    with a_talk(engine) as talk:
        talk.say(A_LINE)
        talk.play_through()
        talk.play_through()
        talk.instance.hosts.issuer.hold_groups(_API_LESS_GROUPS)
        talk.say("Noch eine Zeile.")

        error = talk.until("error")[-1]

        assert error["reason"] == "not-enabled"
        assert len(requests_of(talk)) == 1


def test_an_admin_demoted_mid_talk_is_still_answered(engine: Engine) -> None:
    with a_talk(engine) as talk:
        with talk.instance.hosts.database.begin() as connection:
            connection.execute(
                text("UPDATE users SET role = 'user' WHERE id = :id"), {"id": A.id}
            )
        talk.say(A_LINE)

        assert talk.next_sound()["ai_generated"] is True


def test_the_model_sees_the_lines_fixed_label_and_neither_the_name_nor_the_subject(
    engine: Engine,
) -> None:
    with a_talk(engine) as talk:
        talk.say(A_LINE)
        talk.next_sound()
        (request,) = requests_of(talk)

        asked = json.dumps(request, ensure_ascii=False)

        assert A.username not in asked
        assert A.id not in asked


_REFUSALS: Final = {
    "busy-with-a-wait": ((Reply(429, {"retry-after": "7"}),), None, None),
    "busy-without-a-wait": ((Reply(429),), None, None),
    "budget-spent": (
        (Reply(422, body=a_gateway_error("budget_exceeded")),),
        None,
        None,
    ),
    "gateway-fails": ((Reply(500),), None, None),
    "lease-held-elsewhere": ((), HeldByAnotherPod, None),
    "lease-store-down": ((), UnavailableLimiter, None),
    "not-enabled": ((), None, _outside_the_api_group),
}
_REASONS: Final = {
    "busy-with-a-wait": {"reason": "busy", "retry_after_s": 7},
    "busy-without-a-wait": {"reason": "busy"},
    "budget-spent": {"reason": "budget-spent"},
    "gateway-fails": {},
    "lease-held-elsewhere": {"reason": "busy", "retry_after_s": 1},
    "lease-store-down": {},
    "not-enabled": {"reason": "not-enabled"},
}


@pytest.mark.parametrize("case", _REFUSALS)
def test_a_stopped_decision_sends_one_error_frame_with_its_reason(
    engine: Engine, case: str
) -> None:
    replies, limiter, arrange = _REFUSALS[case]
    options: dict[str, Any] = {}
    if limiter is not None:
        options["limiter"] = limiter
    if arrange is not None:
        options["arrange"] = arrange
    with a_talk(engine, *replies, **options) as talk:
        talk.say(A_LINE)

        error = talk.until("error")[-1]

        assert error == {
            "kind": "error",
            "message": GENERIC_ANSWER_FAILURE,
            **_REASONS[case],
        }


def test_a_line_heard_in_english_is_spoken_in_english(engine: Engine) -> None:
    with a_talk(engine) as talk:
        talk.say("Hello everyone.", language=wire.Language.EN)
        talk.next_sound()

        (spoken, *_) = talk.instance.service.speak_requests

        assert json.loads(spoken.body)["language"] == "en"


def test_a_session_that_ends_while_a_sound_plays_stops_it_and_closes_in_order(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    with json_logged(capsys) as logged, a_talk(engine) as talk:
        talk.say(A_LINE, traceparent=_A_TURN)
        sound = talk.next_sound()
        delete_the_sessions(talk.instance.hosts)
        talk.instance.deadline.release_first()

        after = talk.until("error")
        closed_with = talk.until_closed()

    assert after[0] == {"kind": "stop", "id": sound["id"]}
    assert after[-1] == {
        "kind": "error",
        "message": GENERIC_ANSWER_FAILURE,
        "reason": "session-ended",
    }
    assert closed_with == _NORMAL_CLOSURE
    assert talk.instance.limiter.freed == [talk.session_id]
    (answer,) = events_of(logged, "answer.ended")
    assert (answer["outcome"], answer["reason"], answer["trace_id"]) == (
        "refused",
        "session_ended",
        _A_TRACE,
    )


def test_a_loop_that_raises_ends_the_hearing_and_closes_the_socket_1011(
    engine: Engine,
) -> None:
    with a_talk(engine, limiter=BrokenLimiter) as talk:
        talk.say(A_LINE)

        closed_with = talk.until_closed()

    assert closed_with == _INTERNAL_ERROR


def test_a_stopping_pod_closes_a_talk_with_1012_at_open() -> None:
    stopping = Stopping()
    lobby, private, _clock = a_copresenter_talk_host(stopping=stopping)
    lobby.sign_in(role=Role.ADMIN)
    stopping.begin()

    with (
        lobby.client.websocket_connect(
            f"{TALK}?deck={A_DECK}&language=de", headers={"origin": TALK_ORIGIN}
        ) as socket,
        pytest.raises(WebSocketDisconnect) as closed,
    ):
        socket.receive_text()

    assert closed.value.code == _SERVICE_RESTART
    assert private.speech.hearing_opens == 0


def test_a_decision_writes_one_answer_line_in_the_trace_of_the_line_that_woke_it(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    with json_logged(capsys) as logged, a_talk(engine) as talk:
        talk.say(A_LINE, traceparent=_A_TURN)
        talk.play_through()
        talk.play_through()
        talk.until_freed()

    (answer,) = events_of(logged, "answer.ended")
    (run,) = events_of(logged, "gateway_run.ended")
    assert (answer["outcome"], answer["trace_id"]) == ("ok", _A_TRACE)
    assert isinstance(answer["first_output_ms"], float | int)
    assert run["trace_id"] == _A_TRACE
    assert events_of(logged, "gateway_turn.stopped") == []


def test_a_refused_decision_names_the_reason_in_its_line_and_reaches_no_gateway(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        json_logged(capsys) as logged,
        a_talk(engine, limiter=HeldByAnotherPod) as talk,
    ):
        talk.say(A_LINE, traceparent=_A_TURN)
        talk.until("error")
        # The next decision wakes only after the refused one has ended.
        talk.say("Noch eine Zeile.")
        talk.until("error")

    (answer,) = [
        line
        for line in events_of(logged, "answer.ended")
        if line["trace_id"] == _A_TRACE
    ]
    assert (answer["outcome"], answer["reason"]) == ("refused", "busy")
    assert events_of(logged, "gateway_turn.stopped") == []


def test_a_gateway_stop_writes_one_stop_line_and_ends_the_decision_unavailable(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    with json_logged(capsys) as logged, a_talk(engine, Reply(500)) as talk:
        talk.say(A_LINE, traceparent=_A_TURN)
        talk.until("error")
        talk.leave()

    (answer,) = events_of(logged, "answer.ended")
    assert events_of(logged, "gateway_turn.stopped")
    assert (answer["outcome"], answer["reason"], answer["trace_id"]) == (
        "failed",
        "unavailable",
        _A_TRACE,
    )


def test_a_decision_cut_by_the_browser_leaving_ends_cancelled_in_its_trace(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    with json_logged(capsys) as logged, a_talk(engine) as talk:
        talk.say(A_LINE, traceparent=_A_TURN)
        talk.next_sound()
        talk.leave()

    (answer,) = events_of(logged, "answer.ended")
    assert (answer["outcome"], answer["trace_id"]) == ("cancelled", _A_TRACE)


def test_a_line_that_repeats_the_answer_wakes_nothing_and_is_logged_without_its_text(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    with json_logged(capsys) as logged, a_talk(engine) as talk:
        talk.say(A_LINE)
        talk.play_through()
        talk.play_through()
        talk.say(SENTENCES[0])
        talk.say("Noch eine Zeile.")
        talk.next_sound()

    hearing = events_of(logged, "websocket.ended")[0]["trace_id"]
    (dropped,) = events_of(logged, "echo.dropped")
    (ended,) = events_of(logged, "hearing_session.ended")
    assert last_lines_of(talk) == [A_LINE, "Noch eine Zeile."]
    assert SENTENCES[0] not in json.dumps(dropped)
    assert dropped["trace_id"] == hearing == ended["trace_id"]
    assert hearing not in [
        line["trace_id"] for line in events_of(logged, "answer.ended")
    ]
