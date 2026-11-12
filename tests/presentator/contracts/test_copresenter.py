"""The neutral values shared by the co-presenter path."""

from dataclasses import FrozenInstanceError

import pytest

from presentator.application.copresenter import PreparedAsk
from presentator.contracts.copresenter import (
    CoPresenterReadiness,
    CoPresenterUnavailableError,
    HearingTranscript,
    Question,
    SpeechTokens,
)


def test_copresenter_values_are_closed_and_immutable() -> None:
    question = Question(said="Was ist wichtig?", slide=3, language="de")
    readiness = CoPresenterReadiness(local_hearing_ready=True)
    assert question == Question(said="Was ist wichtig?", slide=3, language="de")
    assert readiness.local_hearing_ready is True
    with pytest.raises(FrozenInstanceError):
        question.slide = 4  # type: ignore[misc]


def test_a_final_transcript_without_its_language_is_refused() -> None:
    with pytest.raises(ValueError, match="language"):
        HearingTranscript(text="gehört", final=True)


def test_expected_private_refusal_has_one_neutral_exception() -> None:
    assert str(CoPresenterUnavailableError()) == "co-presenter unavailable"


def test_the_speech_tokens_are_never_shown_in_a_repr() -> None:
    tokens = SpeechTokens(person="person-MARKER", workload="workload-MARKER")

    assert "MARKER" not in repr(tokens)


def test_a_prepared_ask_hides_the_tokens_it_carries() -> None:
    tokens = SpeechTokens(person="person-MARKER", workload="workload-MARKER")
    prepared = PreparedAsk(
        asked=None,  # type: ignore[arg-type]
        person=None,  # type: ignore[arg-type]
        speech=tokens,
        started=0.0,
    )

    assert "MARKER" not in repr(prepared)
