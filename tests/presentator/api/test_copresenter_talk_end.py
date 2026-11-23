"""How a decision on the loop route ends in the log: spoken, silent or unspoken."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest
from agents.backend.fake_gateway import completion, tool_call, tool_round
from logline import StepTimer, unit_trace
from speech_client import wire

from presentator.api.copresenter import SILENT_ANSWER, AnswerFailure
from tests.support.log_lines import events_of, json_logged
from tests.support.speech_service import a_refusal
from tests.support.talk_drive import (
    A_LINE,
    SENTENCES,
    TracedLimiter,
    a_talk,
    last_lines_of,
)

if TYPE_CHECKING:
    from agents.backend.fake_gateway import Reply
    from sqlalchemy import Engine

    from tests.support.talk_drive import Talking

_A_TRACE: Final = "1" * 32
_A_TURN: Final = f"00-{_A_TRACE}-{'2' * 16}-01"
_ANSWER_START: Final = (
    Path(__file__).resolve().parents[3] / "scripts" / "answer_start.py"
)
_SHOW_SLIDE: Final = tool_call("call-1", "show_slide", {"number": 3})


def _play_every_sound(talk: Talking, *, count: int) -> None:
    for _ in range(count):
        talk.play_through()


@pytest.mark.parametrize(
    ("replies", "sounds", "outcome", "reason", "has_first_output"),
    [
        pytest.param(
            (completion("[silent]"),), 0, "ok", SILENT_ANSWER, False, id="silent-reply"
        ),
        pytest.param(
            (tool_round(_SHOW_SLIDE), completion("[silent]")),
            0,
            "ok",
            SILENT_ANSWER,
            False,
            id="silent-round-after-a-slide-move",
        ),
        pytest.param(
            (tool_round(_SHOW_SLIDE, content=SENTENCES[0]), completion("[silent]")),
            1,
            "ok",
            None,
            True,
            id="spoke-then-silent-after-a-tool-round-is-not-silent",
        ),
        pytest.param(
            (completion(SENTENCES[0]),), 1, "ok", None, True, id="spoken-answer"
        ),
    ],
)
def test_a_decision_the_model_kept_silent_ends_ok_with_the_silent_reason(  # noqa: PLR0913, PLR0917 - one case's expectations
    engine: Engine,
    capsys: pytest.CaptureFixture[str],
    replies: tuple[Reply, ...],
    sounds: int,
    outcome: str,
    reason: str | None,
    *,
    has_first_output: bool,
) -> None:
    with json_logged(capsys) as logged, a_talk(engine, *replies) as talk:
        talk.say(A_LINE, traceparent=_A_TURN)
        _play_every_sound(talk, count=sounds)
        talk.until_freed()

    (answer,) = events_of(logged, "answer.ended")
    assert (answer["outcome"], answer.get("reason")) == (outcome, reason)
    assert ("first_output_ms" in answer) is has_first_output


@pytest.mark.parametrize(
    "speech_outage",
    [
        pytest.param(True, id="speech-outage"),
        pytest.param(False, id="browser-refuses-the-sound"),
    ],
)
def test_an_answer_never_spoken_ends_failed_unspoken_and_the_next_line_still_wakes(
    engine: Engine,
    capsys: pytest.CaptureFixture[str],
    *,
    speech_outage: bool,
) -> None:
    with json_logged(capsys) as logged, a_talk(engine) as talk:
        if speech_outage:
            talk.instance.service.speak_answers.append(
                a_refusal(wire.RefusalCode.UNAVAILABLE)
            )
        talk.say(A_LINE, traceparent=_A_TURN)
        if not speech_outage:
            talk.played(talk.next_sound()["id"], "refused")
        talk.until_freed()
        talk.say("Noch eine Zeile.")
        frames = talk.until("audio")

    (answer,) = [
        line
        for line in events_of(logged, "answer.ended")
        if line["trace_id"] == _A_TRACE
    ]
    assert (answer["outcome"], answer["reason"]) == (
        "failed",
        AnswerFailure.UNSPOKEN,
    )
    assert [event["outcome"] for event in events_of(logged, "speak.ended")][:1] == [
        "failed" if speech_outage else "ok"
    ]
    assert [frame["kind"] for frame in frames].count("audio") == 1
    assert last_lines_of(talk) == [A_LINE, "Noch eine Zeile."]


def test_a_decision_the_browser_cut_frees_the_lease_in_the_waking_trace(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    with json_logged(capsys) as logged, a_talk(engine, limiter=TracedLimiter) as talk:
        talk.say(A_LINE, traceparent=_A_TURN)
        talk.next_sound()
        talk.leave()

    (taken,) = events_of(logged, "slot_take.ended")
    (released,) = events_of(logged, "slot_release.ended")
    assert taken["trace_id"] == released["trace_id"] == _A_TRACE


def test_a_decision_that_ended_frees_the_lease_in_the_waking_trace(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    with json_logged(capsys) as logged, a_talk(engine, limiter=TracedLimiter) as talk:
        talk.say(A_LINE, traceparent=_A_TURN)
        talk.play_through()
        talk.play_through()
        talk.until_freed()

    (taken,) = events_of(logged, "slot_take.ended")
    (released,) = events_of(logged, "slot_release.ended")
    assert taken["trace_id"] == released["trace_id"] == _A_TRACE


def test_the_answer_start_script_reports_a_silent_decision_as_silent(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    with json_logged(capsys) as logged, a_talk(engine, completion("[silent]")) as talk:
        with unit_trace(_A_TURN):
            StepTimer("turn_end_wait.ended", logging.getLogger("speech_service")).end()
        talk.say(A_LINE, traceparent=_A_TURN)
        talk.until_freed()
        recorded = logged.text

    read = subprocess.run(  # noqa: S603 - the interpreter and this repository's own script
        [sys.executable, str(_ANSWER_START)],
        input=recorded,
        capture_output=True,
        text=True,
        check=False,
    )

    assert read.stdout.splitlines() == ["turns 0", f"unjoined {_A_TRACE} silent"]
