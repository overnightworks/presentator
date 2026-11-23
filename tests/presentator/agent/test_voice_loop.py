"""The talk as the voice loop reads it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from presentator.agent.voice_loop import lines_of
from presentator.contracts.copresenter import (
    HeardInTalk,
    HearingTranscript,
    ShownSlide,
)
from tests.presentator.agent.support.driving import on_an_event_loop

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from agents.member import PlaceEntry


def _final(text: str) -> HearingTranscript:
    return HearingTranscript(
        text=text, final=True, language="de", traceparent=f"00-{'1' * 32}-{'2' * 16}-01"
    )


async def _heard(*items: HeardInTalk) -> AsyncGenerator[HeardInTalk]:
    for item in items:
        yield item


async def _entries(*items: HeardInTalk) -> list[PlaceEntry]:
    return [entry async for entry in lines_of(_heard(*items))]


@on_an_event_loop
async def test_each_final_is_one_spoken_line_in_its_language_and_trace() -> None:
    first, second = await _entries(_final("Eins."), _final("Zwei."))

    assert (first.seq, second.seq) == (1, 2)
    assert (first.text, first.spoken, first.act, first.own) == (
        "Eins.",
        True,
        False,
        False,
    )
    assert (first.language, first.traceparent) == ("de", _final("").traceparent)


@on_an_event_loop
async def test_a_slide_that_changed_is_one_act_line_without_a_language() -> None:
    (act,) = await _entries(ShownSlide(number=3))

    assert (act.act, act.spoken, act.language, act.traceparent) == (
        True,
        False,
        None,
        None,
    )
    assert "3" in act.text


@on_an_event_loop
async def test_the_same_slide_again_adds_no_line_and_the_next_slide_adds_one() -> None:
    entries = await _entries(
        ShownSlide(number=2), ShownSlide(number=2), ShownSlide(number=4)
    )

    assert [entry.seq for entry in entries] == [1, 2]


@on_an_event_loop
async def test_the_names_of_a_line_are_the_loops_own_and_a_persons_never() -> None:
    (line,) = await _entries(_final("Eins."))

    assert (line.kind, line.name) == ("talk", "speaker")
