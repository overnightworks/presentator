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
from tests.presentator.api.test_copresenter_answer import a_gateway_error
from tests.support.copresenter_support import delete_the_sessions
from tests.support.deck_text_support import A_DECK_TEXT
from tests.support.log_lines import events_of
from tests.support.talk_drive import (
    A_LINE,
    TALK,
    TWO_SENTENCES,
    HeldElsewhere,
    UnavailableLimiter,
    a_talk,
    closed_at_open,
    lines_in,
    requests_of,
    system_text_of,
)
from tests.support.talk_support import A, B_DECK

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Engine

    from tests.support.copresenter_support import RecordingLimiter
    from tests.support.log_lines import LoggedText
    from tests.support.talk_support import Hosts

_POLICY_VIOLATION: Final = 1008
_NORMAL_CLOSURE: Final = 1000
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
        assert (first["text"], second["text"]) == tuple(TWO_SENTENCES.split(". "))[
            0:1
        ] + (second["text"],)


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
        assert [tool["function"]["name"] for tool in request["tools"]] == [
            "show_slide"
        ]


@pytest.mark.parametrize("deck", ["no-such-deck", B_DECK])
def test_a_deck_the_person_does_not_own_closes_1008_and_asks_nothing(
    engine: Engine, deck: str
) -> None:
    code, requests = closed_at_open(engine, f"{TALK}?deck={deck}&language=de")

    assert (code, requests) == (_POLICY_VIOLATION, 0)


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("deck=a-deck&language=de&other=1", id="a key of no one's"),
        pytest.param("deck=a-deck&deck=a-deck", id="a key twice"),
        pytest.param("language=de&language=en", id="the language twice"),
    ],
)
def test_a_query_with_more_than_the_deck_and_the_language_once_closes_1008(
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
    "budget-spent": ((Reply(422, body=a_gateway_error("budget_exceeded")),), None, None),
    "gateway-fails": ((Reply(500),), None, None),
    "lease-held-elsewhere": ((), HeldElsewhere, None),
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
    engine: Engine, json_logged: LoggedText
) -> None:
    with a_talk(engine) as talk:
        talk.say(A_LINE, traceparent=_A_TURN)
        sound = talk.next_sound()
        delete_the_sessions(talk.instance.hosts)
        talk.instance.deadline.release_first()

        after = talk.until("error")
        with pytest.raises(WebSocketDisconnect) as closed:
            talk.frame()

    assert after[0] == {"kind": "stop", "id": sound["id"]}
    assert after[-1] == {
        "kind": "error",
        "message": GENERIC_ANSWER_FAILURE,
        "reason": "session-ended",
    }
    assert closed.value.code == _NORMAL_CLOSURE
    assert talk.instance.limiter.freed == [talk.instance.limiter.session_ids[0]]
    (answer,) = events_of(json_logged, "answer.ended")
    assert (answer["outcome"], answer["reason"], answer["trace_id"]) == (
        "refused",
        "session_ended",
        _A_TRACE,
    )
