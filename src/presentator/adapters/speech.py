"""Speak and hear through the shared speech service, over its pinned client."""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Final, assert_never

from logline import StepTimer
from pydantic import ValidationError
from speech_client import wire
from speech_client.health import UnexpectedStatus, fetch_health
from speech_client.hearing import (
    OPENING_SECONDS,
    Closed,
    NoToken,
    ReconnectFailed,
    SessionTokens,
)
from speech_client.hearing import Refused as HearingRefused
from speech_client.outcomes import (
    Cut,
    Malformed,
    OverCap,
    PastDeadline,
    Unreachable,
)
from speech_client.relay import AppEnded, BrowserLeft, HopClose, RuleBroken
from speech_client.speak import Busy, Refused, SpeakOutcome, Spoken, speak
from speech_client.utterances import (
    HearingCallbacks,
    Partial,
    ServiceFailed,
    Utterance,
    UtteranceOutcome,
    hear_utterances,
)
from speech_client.wire import VoiceId

from presentator.contracts.copresenter import (
    CoPresenterReadiness,
    CoPresenterUnavailableError,
    HearingTranscript,
    SpeechTokens,
    SpeechTokenSource,
)
from presentator.contracts.voice import SampleLanguage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from speech_client.health import HealthOutcome
    from speech_client.hearing import Reconnected

    from presentator.ports.copresenter import HearingSocket

_LOGGER = logging.getLogger(__name__)
_HEARING_SESSION_EVENT: Final = "hearing_session.ended"
_HEARING_OPENING_EVENT: Final = "hearing_opening.ended"

# These bound presentator's own resources and its patience in a live talk: a
# worker thread's life, one sentence's memory. They vary by no deployment.
_SPEAK_DEADLINE_SECONDS: Final = 30.0
_SPEAK_CAP_BYTES: Final = 8 * 1024 * 1024
_HEALTH_DEADLINE_SECONDS: Final = 5.0
_HEALTH_CAP_BYTES: Final = 64 * 1024
# About 43 s of the 24 kHz PCM16 input: what a hearing holds while the service
# opens or stops taking audio.
_HELD_LIMIT_BYTES: Final = 2 * 1024 * 1024
# Past any talk the operator gives.
_HEARING_DEADLINE_SECONDS: Final = 4 * 60 * 60.0


def voice_speaks(voice: VoiceId, language: SampleLanguage) -> bool:
    """Whether the service's catalogue has `voice` speak `language`."""
    return _client_language(language) in wire.VOICES[voice].languages


def _client_language(language: SampleLanguage) -> wire.Language:
    return wire.Language(language.value)


def _sample_language(language: wire.Language) -> SampleLanguage:
    return SampleLanguage(language.value)


class SpeechSpeaking:
    """Speak one sentence at the shared speech service."""

    def __init__(
        self,
        origin: str,
        *,
        voices: Mapping[SampleLanguage, VoiceId],
        bounded_speak: Callable[..., SpeakOutcome] = speak,
    ) -> None:
        """Keep the service's origin and the voice of each language."""
        self._origin = origin
        self._voices = voices
        self._bounded_speak = bounded_speak

    async def speak(self, text: str, language: str, tokens: SpeechTokens) -> bytes:
        """The sentence as one whole WAV, exactly as the service sent it."""
        spoken = SampleLanguage(language)
        with StepTimer(
            "speak.ended", _LOGGER, language=language, character_count=len(text)
        ) as step:
            try:
                outcome: SpeakOutcome | None = await asyncio.to_thread(
                    self._bounded_speak,
                    self._origin,
                    text,
                    self._voices[spoken],
                    language=_client_language(spoken),
                    response_format=wire.ResponseFormat.WAV,
                    workload_token=tokens.workload,
                    person_token=tokens.person,
                    deadline_seconds=_SPEAK_DEADLINE_SECONDS,
                    cap_bytes=_SPEAK_CAP_BYTES,
                )
            # Start refuses a voice outside the catalogue, and the origin is
            # checked apart, so what the request can still refuse here is the
            # sentence.
            except ValidationError:
                outcome = None
            audio = _audio_of(outcome, step)
        if audio is None:
            raise CoPresenterUnavailableError
        return audio


class SpeakFailure(StrEnum):
    """Why a `speak.ended` line is not `ok`: the closed set of its `reason` codes."""

    SENTENCE_UNSENDABLE = "sentence_unsendable"
    REFUSED = "refused"
    BUSY = "busy"
    CUT = "cut"
    PAST_DEADLINE = "past_deadline"
    OVER_CAP = "over_cap"
    MALFORMED = "malformed"
    UNREACHABLE = "unreachable"


def _audio_of(outcome: SpeakOutcome | None, step: StepTimer) -> bytes | None:
    """The audio, or nothing after the step has recorded why there is none."""
    match outcome:
        case None:
            step.fail(SpeakFailure.SENTENCE_UNSENDABLE)
        case Spoken():
            step.add(audio_bytes=len(outcome.audio))
            return outcome.audio
        case Refused():
            step.add(status=outcome.status, code=outcome.code.value)
            step.fail(SpeakFailure.REFUSED)
        case Busy():
            step.add(retry_after_s=outcome.wait.retry_after_seconds)
            step.refuse(SpeakFailure.BUSY)
        case Cut():
            step.fail(SpeakFailure.CUT)
        case PastDeadline():
            step.fail(SpeakFailure.PAST_DEADLINE)
        case OverCap():
            step.fail(SpeakFailure.OVER_CAP)
        case Malformed():
            step.fail(SpeakFailure.MALFORMED)
        case Unreachable():
            step.fail(SpeakFailure.UNREACHABLE)
        case _:
            assert_never(outcome)
    return None


class SpeechHearing:
    """Read the shared speech service's readiness and hear through it."""

    def __init__(
        self,
        origin: str,
        *,
        voices: Mapping[SampleLanguage, VoiceId],
        bounded_health_fetch: Callable[..., HealthOutcome] = fetch_health,
        hearing_deadline_seconds: float = _HEARING_DEADLINE_SECONDS,
        opening_seconds: float = OPENING_SECONDS,
    ) -> None:
        """Keep the service's origin, the voices readiness asks after and the bounds."""
        self._origin = origin
        self._voices = voices
        self._bounded_health_fetch = bounded_health_fetch
        self._hearing_deadline_seconds = hearing_deadline_seconds
        self._opening_seconds = opening_seconds

    async def readiness(self) -> CoPresenterReadiness:
        """Say whether the service hears, or that a configured voice cannot speak."""
        outcome = await asyncio.to_thread(
            self._bounded_health_fetch,
            self._origin,
            deadline_seconds=_HEALTH_DEADLINE_SECONDS,
            cap_bytes=_HEALTH_CAP_BYTES,
        )
        health = _health_of(outcome)
        if health.hearing.ready and not all(
            _speaks(health, voice) for voice in self._voices.values()
        ):
            _LOGGER.warning("a configured voice cannot speak")
            raise CoPresenterUnavailableError
        return CoPresenterReadiness(
            local_hearing_ready=health.hearing.ready,
        )

    async def relay(
        self,
        browser: HearingSocket,
        language: str,
        tokens: SpeechTokenSource,
        *,
        ended: asyncio.Event,
        heard: Callable[[HearingTranscript], Awaitable[None]],
    ) -> None:
        """Relay the browser's audio to the service and its transcripts back."""
        with StepTimer(_HEARING_SESSION_EVENT, _LOGGER) as session:
            try:
                hears = wire.Language(language)
            except ValueError:
                session.refuse(HearingFailure.UNKNOWN_LANGUAGE)
                await self.refuse(browser)
                return
            session.add(language=hears.value)
            openings = _Openings()
            try:
                outcome = await self._hear(
                    browser, hears, tokens, ended=ended, heard=heard, openings=openings
                )
            # The client leaves the browser socket to the app when the token source
            # raises, so the hearing ends here as a service failure. The token read
            # wrote the error line.
            except CoPresenterUnavailableError:
                openings.end(_tokens_unavailable)
                session.handled()
                _tokens_unavailable(session)
                await self.refuse(browser)
                return
            except Exception as error:
                openings.end_raised(error)
                raise
            else:
                openings.end(lambda opening: _mark_ending(outcome, opening))
                _mark_ending(outcome, session)
            finally:
                openings.end_cancelled()
                session.add(openings=openings.count, utterances=openings.utterances)

    async def _hear(  # noqa: PLR0913 - one parameter per input of the hearing
        self,
        browser: HearingSocket,
        hears: wire.Language,
        tokens: SpeechTokenSource,
        *,
        ended: asyncio.Event,
        heard: Callable[[HearingTranscript], Awaitable[None]],
        openings: _Openings,
    ) -> UtteranceOutcome:
        async def opening_tokens() -> SessionTokens:
            openings.begin()
            read = await tokens()
            return SessionTokens(workload_token=read.workload, person_token=read.person)

        return await hear_utterances(
            browser,
            self._origin,
            token_source=opening_tokens,
            languages=_hearing_languages(hears),
            deadline_seconds=self._hearing_deadline_seconds,
            held_limit_bytes=_HELD_LIMIT_BYTES,
            ended=ended,
            callbacks=_hearing_callbacks(openings, heard),
            opening_seconds=self._opening_seconds,
        )

    async def refuse(self, browser: HearingSocket) -> None:
        """Close the browser as a hearing the service cannot give."""
        await browser.close(HopClose.SERVICE_FAILED)


def _health_of(outcome: HealthOutcome) -> wire.Health:
    match outcome:
        case wire.Health():
            return outcome
        case UnexpectedStatus():
            _LOGGER.warning("readiness failed: status=%d", outcome.status)
        case Malformed() | OverCap() | PastDeadline() | Cut() | Unreachable():
            _LOGGER.warning("readiness failed: %s", type(outcome).__name__)
        case _:
            assert_never(outcome)
    raise CoPresenterUnavailableError


def _speaks(health: wire.Health, voice: VoiceId) -> bool:
    offered = health.speaking.voices.get(voice)
    return offered is not None and (offered.ready or health.speaking.ready)


def _hearing_languages(main: wire.Language) -> tuple[wire.Language, ...]:
    """The languages a hearing asks for: the main one first, then the other one."""
    return (main, *(code for code in wire.Language if code is not main))


class HearingFailure(StrEnum):
    """Why a hearing line is not `ok`: the closed set of its `reason` codes."""

    UNKNOWN_LANGUAGE = "unknown_language"
    TOKENS_UNAVAILABLE = "tokens_unavailable"
    ATTEMPT_FAILED = "attempt_failed"
    REFUSED = "refused"
    SERVICE_FAILED = "service_failed"
    RULE_BROKEN = "rule_broken"
    BUSY = "busy"
    OVER_CAP = "over_cap"
    MALFORMED = "malformed"
    PAST_DEADLINE = "past_deadline"
    CUT = "cut"
    UNREACHABLE = "unreachable"
    SOURCE_EMPTY = "source_empty"
    RECONNECT_FAILED = "reconnect_failed"


class HearingEnding(StrEnum):
    """How a hearing ended in order: the `ending` code of an `ok` line."""

    CLOSED = "closed"
    BROWSER_LEFT = "browser_left"
    APP_ENDED = "app_ended"


class _Openings:
    """The `hearing_opening.ended` line of each opening the hearing tries.

    An opening begins when the client asks for its tokens. The client reports no
    `session.created`, so an opening counts as open at the first thing the hearing
    hands over: a reopening's `Reconnected`, a first opening's first event. The
    client reopens only a session that opened, and announces each reopening that
    opens, so a next token request means the first opening was open and any later
    pending one failed. One still pending when the hearing ends ends as the
    hearing did. The session's line carries the hearing's failure, so an
    opening's is a warning.
    """

    def __init__(self) -> None:
        self.count = 0
        self.utterances = 0
        self._pending: StepTimer | None = None

    def begin(self) -> None:
        if self.count > 1:
            self.end(lambda opening: opening.fail(HearingFailure.ATTEMPT_FAILED))
        self.end(lambda _opening: None)
        self.count += 1
        self._pending = StepTimer(_HEARING_OPENING_EVENT, _LOGGER, attempt=self.count)

    def opened(self, **fields: str | int) -> None:
        if self._pending is not None:
            self._pending.add(**fields)
        self.end(lambda _opening: None)

    def end(self, mark: Callable[[StepTimer], None]) -> None:
        pending, self._pending = self._pending, None
        if pending is not None:
            mark(pending)
            pending.handled()
            pending.end()

    def end_raised(self, error: Exception) -> None:
        pending, self._pending = self._pending, None
        if pending is not None:
            pending.handled()
            pending.__exit__(type(error), error, error.__traceback__)

    def end_cancelled(self) -> None:
        pending, self._pending = self._pending, None
        if pending is not None:
            pending.end_cancelled()


def _hearing_callbacks(
    openings: _Openings, heard: Callable[[HearingTranscript], Awaitable[None]]
) -> HearingCallbacks:
    async def heard_utterance(utterance: Utterance) -> None:
        openings.opened()
        openings.utterances += 1
        await heard(
            HearingTranscript(
                text=utterance.text,
                final=True,
                language=_sample_language(utterance.language),
                traceparent=utterance.traceparent,
            )
        )

    async def heard_partial(partial: Partial) -> None:
        openings.opened()
        await heard(HearingTranscript(text=partial.text, final=False))

    # The client awaits each callback; these two only note the opening as open.
    async def opened(event: object) -> None:  # NOSONAR(S7503)
        del event
        openings.opened()

    async def reconnected(reopened: Reconnected) -> None:  # NOSONAR(S7503)
        openings.opened(
            cause=reopened.cause.value,
            unheard_ms=reopened.unheard_end_ms - reopened.unheard_start_ms,
        )

    return HearingCallbacks(
        on_utterance=heard_utterance,
        on_partial=heard_partial,
        on_speech_started=opened,
        on_turn_ended=opened,
        on_reconnected=reconnected,
    )


def _tokens_unavailable(step: StepTimer) -> None:
    step.fail(HearingFailure.TOKENS_UNAVAILABLE)


_IN_ORDER: Final = {
    Closed: HearingEnding.CLOSED,
    BrowserLeft: HearingEnding.BROWSER_LEFT,
    AppEnded: HearingEnding.APP_ENDED,
}
_PLAIN_FAILURES: Final = {
    OverCap: HearingFailure.OVER_CAP,
    Malformed: HearingFailure.MALFORMED,
    PastDeadline: HearingFailure.PAST_DEADLINE,
    Cut: HearingFailure.CUT,
    Unreachable: HearingFailure.UNREACHABLE,
    NoToken: HearingFailure.SOURCE_EMPTY,
    ReconnectFailed: HearingFailure.RECONNECT_FAILED,
}


def _mark_ending(outcome: UtteranceOutcome, step: StepTimer) -> None:
    """Record on `step` how the hearing ended: its ending, refusal or failure."""
    match outcome:
        case Closed() | BrowserLeft() | AppEnded():
            step.add(ending=_IN_ORDER[type(outcome)])
        case HearingRefused():
            step.add(status=outcome.status)
            if outcome.code is not None:
                step.add(code=outcome.code.value)
            step.fail(HearingFailure.REFUSED)
        case ServiceFailed():
            step.add(code=outcome.code.value)
            step.fail(HearingFailure.SERVICE_FAILED)
        case RuleBroken():
            step.add(close=outcome.close.value)
            step.refuse(HearingFailure.RULE_BROKEN)
        case Busy():
            step.add(retry_after_s=outcome.wait.retry_after_seconds)
            step.refuse(HearingFailure.BUSY)
        case (
            OverCap()
            | Malformed()
            | PastDeadline()
            | Cut()
            | Unreachable()
            | NoToken()
            | ReconnectFailed()
        ):
            step.fail(_PLAIN_FAILURES[type(outcome)])
        case _:
            assert_never(outcome)
