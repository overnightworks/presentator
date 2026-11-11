"""The co-presenter use case prepares and answers an ask over the port fakes."""

import asyncio
import logging
from dataclasses import dataclass, field, replace

import pytest
from speech_client.relay import HopClose

from presentator.application.copresenter import CoPresenterUse, PreparedAsk
from presentator.contracts.copresenter import (
    AccessRefusal,
    AnswerText,
    CoPresenterReadiness,
    CoPresenterUnavailableError,
    Question,
    SpeechTokens,
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


@dataclass
class ClosedBrowser:
    """A browser socket that keeps the code it was closed with."""

    closed_with: list[int] = field(default_factory=list[int])

    async def receive(self) -> dict[str, object]:
        return {"type": "websocket.disconnect"}

    async def close(self, code: int) -> None:
        self.closed_with.append(code)


def test_a_hearing_reads_the_tokens_of_its_session() -> None:
    fakes = PrivateFakes()
    use = a_use(fakes, FakeDeckStore())

    tokens = asyncio.run(use.hearing_tokens("a-session"))

    assert tokens == SpeechTokens(person="a-person", workload="a-workload")
    assert fakes.person_access.sessions == ["a-session"]


@pytest.mark.parametrize("refusal", list(AccessRefusal))
def test_a_refused_token_read_is_unavailable(refusal: AccessRefusal) -> None:
    fakes = PrivateFakes()
    fakes.person_access.refusal = refusal
    use = a_use(fakes, FakeDeckStore())

    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(use.hearing_tokens("a-session"))


def test_a_refused_hearing_closes_the_browser_as_a_service_failure() -> None:
    browser = ClosedBrowser()
    use = a_use(PrivateFakes(), FakeDeckStore())

    asyncio.run(use.refuse_hearing(browser))

    assert browser.closed_with == [HopClose.SERVICE_FAILED]


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
