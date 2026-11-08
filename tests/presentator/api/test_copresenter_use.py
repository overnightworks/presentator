"""The co-presenter use case prepares and answers an ask over the port fakes."""

import asyncio
import logging
from dataclasses import replace

import pytest

from presentator.application.copresenter import CoPresenterUse, PreparedAsk
from presentator.contracts.copresenter import (
    AnswerText,
    CoPresenterReadiness,
    Question,
)
from presentator.contracts.models import Role
from tests.presentator.application.support.fakes import FakeDeckStore
from tests.support.copresenter_support import ASKED, PrivateFakes, a_use

A_QUESTION = Question(said="Frage", slide=1, language=None)
FIRST_AUDIO = "presentator.application.copresenter"


def a_prepared_ask(use: CoPresenterUse) -> PreparedAsk:
    prepared = asyncio.run(
        use.prepare(ASKED, session_id="session", subject="subject", role=Role.ADMIN)
    )
    assert isinstance(prepared, PreparedAsk)
    return prepared


async def _read_until_done(use: CoPresenterUse, prepared: PreparedAsk) -> list[object]:
    async with use.answer(prepared, A_QUESTION) as events:
        return [event async for event in events]


def test_cancelling_an_answer_awaits_the_private_operation() -> None:
    """Unchanged behaviour: cancelling an answer awaits the private operation."""
    asyncio.run(_cancel_answer())


async def _cancel_answer() -> None:
    fakes = PrivateFakes()
    fakes.answers.hold = True
    use = a_use(fakes, FakeDeckStore())
    prepared = await use.prepare(
        ASKED, session_id="session", subject="subject", role=Role.ADMIN
    )
    assert isinstance(prepared, PreparedAsk)

    async with use.answer(prepared, A_QUESTION) as events:
        assert await anext(events) == AnswerText(text="Eine Antwort.")

    assert fakes.answers.exits == 1


def test_ending_hearing_closes_and_awaits_the_private_child() -> None:
    """Unchanged behaviour: ending a hearing closes its child."""
    asyncio.run(_end_hearing())


async def _end_hearing() -> None:
    fakes = PrivateFakes()
    use = a_use(fakes, FakeDeckStore())

    async with use.hear("de") as hearing:
        await hearing.send_pcm(b"pcm")

    assert fakes.speech.hearing.frames == [b"pcm"]
    assert fakes.speech.hearing.closed == 1


def test_readiness_comes_from_speech_and_never_touches_the_answer_port() -> None:
    """Unchanged behaviour: readiness comes from speech alone."""
    fakes = PrivateFakes()

    readiness = asyncio.run(a_use(fakes, FakeDeckStore()).readiness())

    assert readiness == CoPresenterReadiness(
        hearing_sample_rate=16_000, local_hearing_ready=True
    )
    assert fakes.speech.readiness_calls == 1
    assert (fakes.answers.messages, fakes.answers.exits) == ([], 0)


def test_the_first_audio_is_logged_once_with_its_delay_and_no_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Decision 9: one line, counted from `started`."""
    fakes = PrivateFakes()
    fakes.answers.deltas = ["Erster Satz. ", "Zweiter Satz."]
    ticks = iter([10.0, 10.25, 99.0])
    use = replace(a_use(fakes, FakeDeckStore()), monotonic=lambda: next(ticks))
    caplog.set_level(logging.INFO, logger=FIRST_AUDIO)

    asyncio.run(_read_until_done(use, a_prepared_ask(use)))

    assert [record.getMessage() for record in caplog.records] == [
        "co-presenter first audio: ms=250"
    ]


def test_an_answer_without_audio_logs_no_first_audio(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Decision 9, cell 27."""
    fakes = PrivateFakes()
    fakes.answers.deltas = []
    use = a_use(fakes, FakeDeckStore())
    caplog.set_level(logging.INFO, logger=FIRST_AUDIO)

    asyncio.run(_read_until_done(use, a_prepared_ask(use)))

    assert caplog.records == []
