"""The loop route holds the session's ask lease from a decision's turn to its end."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Final

from tests.support.talk_drive import A_LINE, HeldByAnotherPod, a_talk

if TYPE_CHECKING:
    from sqlalchemy import Engine

_NORMAL_CLOSURE: Final = 1000


def test_a_decision_in_flight_holds_the_session_so_an_ask_is_refused(
    engine: Engine,
) -> None:
    with a_talk(engine) as talk:
        talk.say(A_LINE)
        talk.next_sound()

        assert talk.ask() == HTTPStatus.TOO_MANY_REQUESTS


def test_the_lease_is_free_again_once_the_last_sound_was_played(
    engine: Engine,
) -> None:
    with a_talk(engine) as talk:
        talk.say(A_LINE)
        talk.play_through()
        talk.play_through()
        talk.say("Noch eine Zeile.")
        talk.next_sound()

        assert talk.instance.limiter.freed == [talk.session_id]


def test_the_lease_is_free_at_once_after_the_browser_leaves_mid_sound(
    engine: Engine,
) -> None:
    with a_talk(engine) as talk:
        talk.say(A_LINE)
        talk.next_sound()
        talk.leave()

        assert talk.instance.limiter.freed == [talk.session_id]


def test_the_lease_is_free_after_the_service_closed_the_hearing(
    engine: Engine,
) -> None:
    with a_talk(engine) as talk:
        talk.say(A_LINE)
        talk.next_sound()
        talk.cues.close()

        closed_with = talk.until_closed()

    assert closed_with == _NORMAL_CLOSURE
    assert talk.instance.limiter.freed == [talk.session_id]


def test_a_decision_refused_busy_frees_nothing_of_the_other_holder(
    engine: Engine,
) -> None:
    with a_talk(engine, limiter=HeldByAnotherPod) as talk:
        talk.say(A_LINE)
        talk.until("error")

    assert talk.instance.limiter.freed == []
