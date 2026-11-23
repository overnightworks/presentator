"""The talk as the voice loop reads it."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from agents.backend import TurnStopKind
from agents.turn import FailureCode
from agents.voice import (
    DecisionEnded,
    DecisionFailed,
    ReplyUnreadable,
    SentenceRefused,
    SentenceStarted,
    StayedSilent,
)

from presentator.agent.voice_loop import TalkLease, lines_of, told
from presentator.contracts.copresenter import (
    DecisionEnd,
    HeardInTalk,
    HearingTranscript,
    ShownSlide,
)
from presentator.contracts.copresenter import DecisionEnded as ContractDecisionEnded
from tests.presentator.agent.support.driving import on_an_event_loop

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from agents.member import PlaceEntry
    from agents.voice import VoiceEvent

    from presentator.contracts.copresenter import RetryAfter, TalkEvent


_FAILED = DecisionFailed(
    TurnStopKind.FAILED, FailureCode.PROVIDER_UNAVAILABLE, detail=None
)


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


class _SlowTicket:
    """A ticket whose release ends only when the test lets it."""

    session_id = "session"

    def __init__(self) -> None:
        self.may_end = asyncio.Event()
        self.released = False

    async def release(self) -> None:
        await self.may_end.wait()
        self.released = True


class _OneTicketLimiter:
    def __init__(self, ticket: _SlowTicket) -> None:
        self._ticket = ticket

    async def admit(self, session_id: str) -> _SlowTicket | RetryAfter:
        del session_id
        return self._ticket


@on_an_event_loop
async def test_a_cancelled_take_leaves_the_release_before_it_running() -> None:
    ticket = _SlowTicket()
    lease = TalkLease(_OneTicketLimiter(ticket), ticket.session_id)
    await lease.take()
    lease.end_span()

    taking = asyncio.create_task(lease.take())
    await asyncio.sleep(0)
    taking.cancel()
    await asyncio.wait({taking})
    closing = asyncio.create_task(lease.close())
    await asyncio.sleep(0)
    assert not closing.done()
    ticket.may_end.set()
    await closing

    assert ticket.released


@pytest.mark.parametrize(
    ("events", "how"),
    [
        pytest.param((StayedSilent(),), DecisionEnd.SILENT, id="silent"),
        pytest.param(
            (SentenceStarted("Eins."), StayedSilent()),
            DecisionEnd.SPOKE,
            id="spoke-then-silent",
        ),
        pytest.param((SentenceStarted("Eins."),), DecisionEnd.SPOKE, id="spoke"),
        pytest.param(
            (SentenceStarted("Eins."), SentenceRefused("Zwei.")),
            DecisionEnd.UNSPOKEN,
            id="a-later-sentence-refused",
        ),
        pytest.param(
            (SentenceRefused("Eins."),), DecisionEnd.UNSPOKEN, id="first-refused"
        ),
        pytest.param((ReplyUnreadable(),), DecisionEnd.NOTHING, id="unreadable"),
        pytest.param(
            (StayedSilent(), _FAILED), DecisionEnd.NOTHING, id="silent-but-failed"
        ),
    ],
)
def test_a_decision_ends_as_what_it_told_of_its_sound(
    events: tuple[VoiceEvent, ...], how: DecisionEnd
) -> None:
    reported: list[TalkEvent] = []
    tell = told(reported.append)

    for event in (*events, DecisionEnded()):
        tell(event)

    assert reported[-1] == ContractDecisionEnded(how)


def test_each_decision_is_told_apart_from_the_one_before() -> None:
    reported: list[TalkEvent] = []
    tell = told(reported.append)

    for event in (SentenceStarted("Eins."), DecisionEnded(), StayedSilent()):
        tell(event)
    tell(DecisionEnded())

    assert [
        event.how for event in reported if isinstance(event, ContractDecisionEnded)
    ] == [
        DecisionEnd.SPOKE,
        DecisionEnd.SILENT,
    ]
