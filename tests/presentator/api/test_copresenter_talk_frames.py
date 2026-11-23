"""The loop route's frames: what the browser sends up and what goes down."""

from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Final, cast

import pytest
from agents.backend.fake_gateway import completion, tool_call, tool_round
from speech_client import wire

from presentator.api.copresenter_talk import (
    PlayedFrame,
    ShownFrame,
    SoundEnd,
    TalkSounds,
    read_up_frame,
)
from presentator.contracts.copresenter import Audio, UnreadableTalkMessageError
from tests.support.log_lines import events_of, json_logged
from tests.support.speech_service import a_delta
from tests.support.talk_drive import (
    A_LINE,
    SENTENCES,
    a_talk,
    last_lines_of,
    lines_in,
    requests_of,
)

if TYPE_CHECKING:
    from sqlalchemy import Engine

_POLICY_VIOLATION: Final = 1008
_THREE_SENTENCES: Final = ("Eins.", "Zwei.", "Drei.")


@pytest.mark.parametrize(
    ("text", "frame"),
    [
        pytest.param(
            '{"kind":"played","id":3,"end":"ended"}',
            PlayedFrame(kind="played", id=3, end=SoundEnd.ENDED),
            id="a sound that ended",
        ),
        pytest.param(
            '{"kind":"played","id":1,"end":"refused"}',
            PlayedFrame(kind="played", id=1, end=SoundEnd.REFUSED),
            id="a sound the browser refused",
        ),
        pytest.param(
            '{"kind":"played","id":1,"end":"failed"}',
            PlayedFrame(kind="played", id=1, end=SoundEnd.FAILED),
            id="a sound that failed",
        ),
        pytest.param(
            '{"kind":"shown","slide":4}',
            ShownFrame(kind="shown", slide=4),
            id="a shown slide",
        ),
    ],
)
def test_a_readable_up_frame_is_read_as_the_frame_it_names(
    text: str, frame: PlayedFrame | ShownFrame
) -> None:
    assert read_up_frame(text) == frame


_UNREADABLE: Final = {
    "a list": "[1]",
    "no JSON": "nope",
    "a number": "7",
    "null": "null",
    "a string": '"played"',
    "an unknown kind": '{"kind":"paused"}',
    "no kind": '{"id":1,"end":"ended"}',
    "an extra field": '{"kind":"shown","slide":1,"extra":true}',
    "a missing field": '{"kind":"played","id":1}',
    "an id that is text": '{"kind":"played","id":"1","end":"ended"}',
    "an id that is a boolean": '{"kind":"played","id":true,"end":"ended"}',
    "an end of no one's": '{"kind":"played","id":1,"end":"stopped"}',
    "a slide below one": '{"kind":"shown","slide":0}',
    "a slide that is text": '{"kind":"shown","slide":"2"}',
    "a slide that is a fraction": '{"kind":"shown","slide":1.5}',
}


@pytest.mark.parametrize("text", _UNREADABLE.values(), ids=_UNREADABLE.keys())
def test_an_up_frame_that_is_no_frame_of_the_protocol_is_an_unreadable_message(
    text: str,
) -> None:
    with pytest.raises(UnreadableTalkMessageError):
        read_up_frame(text)


_ONE_OF_EACH_KIND: Final = (
    "no JSON",
    "an unknown kind",
    "an extra field",
    "an id that is text",
    "an end of no one's",
)


@pytest.mark.parametrize(
    "text",
    [_UNREADABLE[kind] for kind in _ONE_OF_EACH_KIND],
    ids=_ONE_OF_EACH_KIND,
)
def test_an_unreadable_up_frame_closes_1008_and_ends_the_hearing_refused(
    engine: Engine, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    with json_logged(capsys) as logged, a_talk(engine) as talk:
        talk.say(A_LINE)
        talk.next_sound()
        talk.up_text(text)

        closed_with = talk.until_closed()

    (ended,) = events_of(logged, "hearing_session.ended")
    assert closed_with == _POLICY_VIOLATION
    assert (ended["outcome"], ended["reason"], "error_type" in ended) == (
        "refused",
        "unreadable_message",
        False,
    )
    assert talk.instance.limiter.freed == [talk.session_id]


def test_binary_frames_reach_the_service_unchanged_with_text_frames_between_them(
    engine: Engine,
) -> None:
    with a_talk(engine) as talk:
        talk.microphone(b"one")
        talk.shown(2)
        talk.microphone(b"two")

        audio = [
            event["audio"]
            for event in talk.instance.service.events
            if event["type"] == "input_audio_buffer.append"
        ]

    assert audio[-2:] == [
        base64.b64encode(b"one").decode("ascii"),
        base64.b64encode(b"two").decode("ascii"),
    ]


def test_each_transcript_goes_down_as_one_heard_frame(engine: Engine) -> None:
    with a_talk(engine) as talk:
        talk.cues.send(a_delta("Hallo zu"))
        talk.say(A_LINE, language=wire.Language.EN)

        interim, final = talk.frame(), talk.frame()

    assert interim == {
        "kind": "heard",
        "text": "Hallo zu",
        "final": False,
        "language": None,
    }
    assert final == {"kind": "heard", "text": A_LINE, "final": True, "language": "en"}


def _act_lines(request: dict[str, Any]) -> list[dict[str, Any]]:
    return [line for line in lines_in(request) if line.get("act")]


def test_a_shown_frame_enters_the_talk_as_one_act_line_and_the_same_slide_adds_none(
    engine: Engine,
) -> None:
    with a_talk(engine) as talk:
        talk.shown(2)
        talk.shown(2)
        talk.microphone()
        talk.say(A_LINE)
        talk.play_through()
        talk.play_through()
        talk.shown(2)
        talk.microphone()
        talk.say("Noch eine Zeile.")
        talk.next_sound()

        first, second = requests_of(talk)

    assert len(_act_lines(first)) == 1
    assert _act_lines(second) == []


def test_a_slide_the_model_shows_goes_down_as_a_slide_frame(engine: Engine) -> None:
    replies = (
        tool_round(tool_call("call-1", "show_slide", {"number": 3})),
        completion(SENTENCES[0]),
    )
    with a_talk(engine, *replies) as talk:
        talk.say(A_LINE)

        frames = talk.until("audio")

    assert {"kind": "slide", "number": 3} in frames


def test_a_failed_slide_move_goes_down_as_no_frame(engine: Engine) -> None:
    replies = (
        tool_round(tool_call("call-1", "show_slide", {"number": 0})),
        completion(SENTENCES[0]),
    )
    with a_talk(engine, *replies) as talk:
        talk.say(A_LINE)

        frames = talk.until("audio")

    assert "slide" not in [frame["kind"] for frame in frames]


def test_a_refused_sound_ends_the_answer_and_the_next_line_still_wakes_a_decision(
    engine: Engine,
) -> None:
    with a_talk(engine) as talk:
        talk.say(A_LINE)
        first = talk.next_sound()
        talk.played(first["id"], "refused")
        talk.say("Noch eine Zeile.")

        second = talk.next_sound()

    assert (first["text"], second["text"]) == (SENTENCES[0], SENTENCES[0])
    assert last_lines_of(talk) == [A_LINE, "Noch eine Zeile."]


def test_a_played_frame_of_a_sound_never_sent_changes_nothing(engine: Engine) -> None:
    with a_talk(engine) as talk:
        talk.say(A_LINE)
        first = talk.next_sound()
        talk.played(cast("int", first["id"]) + 41)
        talk.played(first["id"], "refused")
        talk.say("Noch eine Zeile.")

        after = talk.next_sound()

    assert after["text"] == SENTENCES[0]


def test_a_played_frame_of_a_sound_already_ended_changes_nothing_and_keeps_the_socket(
    engine: Engine,
) -> None:
    with a_talk(engine, completion(" ".join(_THREE_SENTENCES))) as talk:
        talk.say(A_LINE)
        first = talk.play_through()
        second = talk.next_sound()
        talk.played(first["id"], "refused")
        talk.played(second["id"])
        talk.say("Noch eine Zeile.")

        third = talk.until("audio")[-1]

    assert (second["text"], third["text"]) == (_THREE_SENTENCES[1], _THREE_SENTENCES[2])


@pytest.mark.parametrize(
    "cut", [False, True], ids=["after the report", "after the cut"]
)
def test_a_played_report_before_the_play_resumed_is_ignored(*, cut: bool) -> None:
    """Buffered reports reach the route before the play they end has resumed."""

    async def report_twice() -> None:
        browser = TalkSounds(lambda _frame: None, lambda: None)
        playing = asyncio.create_task(browser.play(Audio(text="Satz.", wav=b"")))
        await asyncio.sleep(0)
        if cut:
            playing.cancel()
        else:
            browser.played(1, SoundEnd.ENDED)
        browser.played(1, SoundEnd.ENDED)
        with suppress(asyncio.CancelledError):
            await playing

    asyncio.run(report_twice())


def test_a_played_report_while_no_sound_plays_leaves_the_next_sound_waiting() -> None:
    async def report_then_play() -> bool:
        browser = TalkSounds(lambda _frame: None, lambda: None)
        browser.played(1, SoundEnd.ENDED)
        playing = asyncio.create_task(browser.play(Audio(text="Satz.", wav=b"")))
        await asyncio.sleep(0)
        waiting = not playing.done()
        browser.played(1, SoundEnd.ENDED)
        await playing
        return waiting

    assert asyncio.run(report_then_play())


def test_an_audio_frame_carries_the_sentence_and_its_sound(engine: Engine) -> None:
    with a_talk(engine) as talk:
        talk.say(A_LINE)

        sound = talk.next_sound()

    assert (sound["id"], sound["text"]) == (1, SENTENCES[0])
    assert base64.b64decode(cast("str", sound["wav_b64"])).startswith(b"RIFF")
