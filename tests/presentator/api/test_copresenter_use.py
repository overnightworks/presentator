"""The co-presenter use case prepares and answers an ask over the port fakes."""

import asyncio

from presentator.application.copresenter import PreparedAsk
from presentator.contracts.copresenter import (
    AnswerText,
    CoPresenterReadiness,
    Question,
)
from presentator.contracts.models import Role
from tests.presentator.application.support.fakes import FakeDeckStore
from tests.support.copresenter_support import ASKED, PrivateFakes, a_use

A_QUESTION = Question(said="Frage", slide=1, language="de")


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


def test_readiness_comes_from_speech_and_never_touches_the_answer_port() -> None:
    """Unchanged behaviour: readiness comes from speech alone."""
    fakes = PrivateFakes()

    readiness = asyncio.run(a_use(fakes, FakeDeckStore()).readiness())

    assert readiness == CoPresenterReadiness(local_hearing_ready=True)
    assert fakes.speech.readiness_calls == 1
    assert (fakes.answers.messages, fakes.answers.exits) == ([], 0)
