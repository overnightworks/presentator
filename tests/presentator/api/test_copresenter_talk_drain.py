"""A stopping pod lets a running decision end, then closes the talk 1012."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from presentator.api.copresenter import CHECK_INTERVAL_SECONDS
from tests.support.copresenter_support import delete_the_sessions
from tests.support.log_lines import events_of, json_logged
from tests.support.talk_drive import A_LINE, SENTENCES, a_talk, requests_of

if TYPE_CHECKING:
    import pytest
    from sqlalchemy import Engine

_NORMAL_CLOSURE: Final = 1000
_SERVICE_RESTART: Final = 1012
_A_TRACE: Final = "1" * 32
_A_TURN: Final = f"00-{_A_TRACE}-{'2' * 16}-01"


def _kinds(frames: list[dict[str, object]]) -> list[object]:
    return [frame["kind"] for frame in frames]


def test_a_stop_that_begins_while_a_sound_plays_lets_the_decision_end(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    with json_logged(capsys) as logged, a_talk(engine) as talk:
        talk.say(A_LINE, traceparent=_A_TURN)
        first = talk.next_sound()
        talk.stop()
        talk.played(first["id"])
        second = talk.next_sound()
        talk.played(second["id"])
        talk.until_closed()

    (answer,) = events_of(logged, "answer.ended")
    spoken = [first["text"], second["text"]]
    assert spoken == list(SENTENCES)
    assert talk.instance.limiter.freed == [talk.session_id]
    assert (answer["outcome"], answer["trace_id"]) == ("ok", _A_TRACE)
    assert isinstance(answer["first_output_ms"], float | int)


def test_a_line_heard_after_the_stop_wakes_no_decision_and_sends_no_error(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    with json_logged(capsys) as logged, a_talk(engine) as talk:
        talk.say(A_LINE)
        first = talk.next_sound()
        talk.stop()
        talk.instance.deadline.waiting(talk.instance.drain_seconds)
        talk.say("Noch eine Zeile.")
        talk.until("heard")
        talk.played(first["id"])
        talk.play_through()

        frames, closed_with = talk.frames_until_closed()

    assert closed_with == _SERVICE_RESTART
    assert "error" not in _kinds(frames)
    assert "audio" not in _kinds(frames)
    assert len(requests_of(talk)) == 1
    assert len(events_of(logged, "answer.ended")) == 1


def test_the_talk_closes_1012_as_soon_as_the_drained_decision_ends(
    engine: Engine,
) -> None:
    with a_talk(engine) as talk:
        talk.say(A_LINE)
        first = talk.next_sound()
        talk.stop()
        talk.played(first["id"])
        talk.play_through()

        frames, closed_with = talk.frames_until_closed()

    assert closed_with == _SERVICE_RESTART
    assert "error" not in _kinds(frames)
    assert talk.instance.limiter.freed == [talk.session_id]


def test_a_decision_that_ends_on_a_pod_that_is_not_stopping_closes_nothing(
    engine: Engine,
) -> None:
    with a_talk(engine) as talk:
        talk.say(A_LINE)
        talk.play_through()
        talk.play_through()
        talk.say("Noch eine Zeile.")

        assert talk.next_sound()["text"] == SENTENCES[0]


def test_a_stopping_pod_closes_a_talk_with_no_decision_1012_within_one_poll(
    engine: Engine,
) -> None:
    with a_talk(engine) as talk:
        talk.stop()

        frames, closed_with = talk.frames_until_closed()

    assert closed_with == _SERVICE_RESTART
    assert "error" not in _kinds(frames)


def test_a_decision_the_browser_never_reports_played_is_cut_at_the_limit(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    with json_logged(capsys) as logged, a_talk(engine) as talk:
        talk.say(A_LINE, traceparent=_A_TURN)
        sound = talk.next_sound()
        talk.stop()
        talk.past_the_drain_limit()

        frames, closed_with = talk.frames_until_closed()

    (answer,) = events_of(logged, "answer.ended")
    assert closed_with == _SERVICE_RESTART
    assert {"kind": "stop", "id": sound["id"]} in frames
    assert talk.instance.limiter.freed == [talk.session_id]
    assert (answer["outcome"], answer["trace_id"]) == ("cancelled", _A_TRACE)


def test_a_session_that_ends_during_the_drain_ends_the_talk_as_it_would_at_any_time(
    engine: Engine,
) -> None:
    with a_talk(engine) as talk:
        talk.say(A_LINE)
        talk.next_sound()
        talk.stop()
        talk.instance.deadline.waiting(talk.instance.drain_seconds)
        talk.instance.deadline.waiting(CHECK_INTERVAL_SECONDS)
        delete_the_sessions(talk.instance.hosts)
        talk.instance.deadline.release_check()

        frames, closed_with = talk.frames_until_closed()

    assert closed_with == _NORMAL_CLOSURE
    assert frames[-1]["reason"] == "session-ended"
