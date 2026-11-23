"""Runs a talk through the client's voice loop: the model hears and answers."""

from __future__ import annotations

import asyncio
import time
from contextlib import aclosing
from functools import partial
from typing import TYPE_CHECKING, Final, assert_never

import agents.voice as agents_voice
from agents.agent_file import load_agent
from agents.backend import TurnStopKind, TurnStopped
from agents.backend.gateway import GatewayBackend
from agents.member import PlaceEntry
from agents.turn import FailureCode, RejectionCode, StopOutcome
from agents.voice.loop import run_voice_loop

from presentator.agent.backend import (
    COPRESENTER_AGENT_FILE,
    SHOW_SLIDE,
    RunSummary,
    SlideMoves,
    log_stopped,
    turn_person,
)
from presentator.contracts.copresenter import (
    AccessRefusal,
    AnswerStopReason,
    Audio,
    CoPresenterUnavailableError,
    DecisionBegan,
    DecisionEnded,
    DecisionStopped,
    EchoDropped,
    HeardInTalk,
    HearingTranscript,
    RetryAfter,
    ShownSlide,
    SoundRefusedError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable

    import httpx2
    from agents.agent_file import TurnRequest
    from agents.backend import AgentBackend, TurnEvent, TurnPerson
    from agents.member import SpokenLanguage
    from webauth.oidc import OidcRelyingParty

    from presentator.agent.settings import AgentSettings
    from presentator.contracts.copresenter import TalkEvent
    from presentator.contracts.words import Language
    from presentator.ports.copresenter import (
        AskLimiter,
        AskTicket,
        Talk,
        TalkBrowser,
    )

# The lines' labels are the model's own view of the talk, never a person's name.
_PLACE_KIND: Final = "talk"
_SPEAKER: Final = "speaker"
_LEASE_DETAIL: Final = "ask_lease"
_ACCESS_DETAIL: Final = "person_access"
_REASONS: Final = {
    StopOutcome.NOT_ENABLED: AnswerStopReason.NOT_ENABLED,
    StopOutcome.BUSY: AnswerStopReason.BUSY,
    StopOutcome.BUDGET_SPENT: AnswerStopReason.BUDGET_SPENT,
    StopOutcome.UNAVAILABLE: None,
}


class TalkLease:
    """The session's ask lease for one talk: held from a decision's turn to its end.

    It holds at most one ticket. The release of an ended decision's ticket runs on
    its own, so the loop's synchronous report can start it; the next take and the
    close wait for it.
    """

    def __init__(self, limiter: AskLimiter, session_id: str) -> None:
        """Take the session's lease from this limiter."""
        self._limiter = limiter
        self._session_id = session_id
        self._ticket: AskTicket | None = None
        self._releasing: asyncio.Task[None] | None = None

    async def take(self) -> None:
        """Hold the lease for a decision's turn.

        Raises TurnStopped when another holder has it or the store does not answer.
        """
        await self._released()
        try:
            admitted = await self._limiter.admit(self._session_id)
        except CoPresenterUnavailableError:
            raise TurnStopped(
                TurnStopKind.FAILED,
                FailureCode.PROVIDER_UNAVAILABLE,
                detail=_LEASE_DETAIL,
            ) from None
        if isinstance(admitted, RetryAfter):
            raise TurnStopped(
                TurnStopKind.REFUSED,
                RejectionCode.BUSY,
                retry_after_s=admitted.seconds,
                detail=_LEASE_DETAIL,
            )
        self._ticket = admitted

    def end_span(self) -> None:
        """Free the lease a decision held; one it was refused holds nothing."""
        ticket, self._ticket = self._ticket, None
        if ticket is not None:
            self._releasing = asyncio.create_task(ticket.release())

    async def close(self) -> None:
        """Free the lease whatever ended the talk; a cancelled caller still frees it."""
        self.end_span()
        await self._released()

    async def _released(self) -> None:
        """Wait for the release; a cancelled waiter leaves it running."""
        if self._releasing is not None:
            await asyncio.shield(self._releasing)
            self._releasing = None


class LeasedBackend:
    """The gateway backend with the talk's lease and its logs around each turn."""

    def __init__(
        self,
        gateway: AgentBackend,
        lease: TalkLease,
        browser: TalkBrowser,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Run turns on this gateway, each under the talk's lease."""
        self._gateway = gateway
        self._lease = lease
        self._browser = browser
        self._monotonic = monotonic

    async def run(
        self, request: TurnRequest, person: TurnPerson
    ) -> AsyncGenerator[TurnEvent]:
        """Stream a turn the lease admits; a refusal is a stopped turn."""
        await self._lease.take()
        summary = RunSummary(self._monotonic)
        moves = SlideMoves()
        try:
            async with aclosing(self._gateway.run(request, person)) as events:
                async for event in events:
                    summary.note(event)
                    if (number := moves.moved(event)) is not None:
                        summary.note_move()
                        self._browser.show_slide(number)
                    yield event
        except TurnStopped as stopped:
            log_stopped(stopped)
            raise
        finally:
            if summary.has_events:
                summary.log()


async def lines_of(heard: AsyncIterator[HeardInTalk]) -> AsyncGenerator[PlaceEntry]:
    """The talk as the loop reads it: lines, and an act line for each new slide."""
    shown: int | None = None
    seq = 0
    async for item in heard:
        match item:
            case HearingTranscript():
                seq += 1
                yield PlaceEntry(
                    seq=seq,
                    kind=_PLACE_KIND,
                    name=_SPEAKER,
                    text=item.text,
                    own=False,
                    spoken=True,
                    language=item.language,
                    traceparent=item.traceparent,
                )
            case ShownSlide() if item.number != shown:
                seq += 1
                shown = item.number
                yield PlaceEntry(
                    seq=seq,
                    kind=_PLACE_KIND,
                    name=_SPEAKER,
                    text=f"The deck shows slide {item.number}.",
                    own=False,
                    act=True,
                    traceparent=None,
                )
            case ShownSlide():
                continue
            case _:
                assert_never(item)


class TalkVoice:
    """The model's voice: the speech service speaks, the browser plays."""

    def __init__(
        self,
        speak: Callable[[str, str], Awaitable[bytes]],
        browser: TalkBrowser,
        controls_language: Language,
    ) -> None:
        """Speak with this function, play on this browser, default to this language."""
        self._speak = speak
        self._browser = browser
        self._controls_language = controls_language

    async def render(self, sentence: str, *, language: SpokenLanguage | None) -> Audio:
        """The sentence as sound; a speech outage ends the answer."""
        try:
            wav = await self._speak(sentence, language or self._controls_language)
        except CoPresenterUnavailableError:
            raise agents_voice.VoiceRefused from None
        return Audio(text=sentence, wav=wav)

    async def play(self, audio: Audio) -> None:
        """Play the sound and return when the browser says it ended."""
        try:
            await self._browser.play(audio)
        except SoundRefusedError:
            raise agents_voice.VoiceRefused from None

    async def pause(self) -> None:  # pragma: no cover - only a talk-over pauses
        """Refuse: nothing here is held."""
        raise agents_voice.VoiceRefused

    async def resume(self) -> None:  # pragma: no cover - only a talk-over resumes
        """Refuse: nothing here is held."""
        raise agents_voice.VoiceRefused


def _every_line(line: PlaceEntry, *, first_after_answer: bool) -> bool:
    """The model decides itself whether a line needs an answer."""
    del line, first_after_answer
    return True


def _told(
    report: Callable[[TalkEvent], None],
) -> Callable[[agents_voice.VoiceEvent], None]:
    """The loop's events as the route's records; the others tell it nothing."""

    def tell(event: agents_voice.VoiceEvent) -> None:
        match event:
            case agents_voice.Woke():
                report(DecisionBegan())
            case agents_voice.DecisionFailed():
                report(
                    DecisionStopped(
                        reason=_REASONS[event.outcome],
                        retry_after_s=event.retry_after_s,
                    )
                )
            case agents_voice.DecisionEnded():
                report(DecisionEnded())
            case agents_voice.EchoDropped():
                report(EchoDropped())
            case _:
                return

    return tell


class VoiceLoopTalks:
    """Runs each talk through the voice loop over the gateway."""

    def __init__(self, gateway: AgentBackend, relying_party: OidcRelyingParty) -> None:
        """Run turns on this backend, for this relying party's people."""
        self._gateway = gateway
        self._relying_party = relying_party
        self._agent = load_agent(COPRESENTER_AGENT_FILE, [SHOW_SLIDE.declaration])

    async def run(self, talk: Talk) -> None:
        """Hear and answer until the hearing ends; the lease is free when it returns."""
        lease = TalkLease(talk.limiter, talk.session_id)
        report = _told(talk.report)

        def reported(event: agents_voice.VoiceEvent) -> None:
            if isinstance(event, agents_voice.DecisionEnded):
                lease.end_span()
            report(event)

        try:
            await run_voice_loop(
                self._agent,
                LeasedBackend(self._gateway, lease, talk.browser),
                turn_person(
                    self._relying_party,
                    subject=talk.subject,
                    role=talk.role,
                    groups=partial(_groups_of, talk),
                ),
                lines_of(talk.hearing),
                TalkVoice(talk.speak, talk.browser, talk.controls_language),
                wakes=_every_line,
                context=talk.context,
                report=reported,
            )
        finally:
            await lease.close()


async def _groups_of(talk: Talk) -> frozenset[str]:
    """The person's groups now; a refusal or an unreadable store stops the turn."""
    try:
        groups = await talk.groups()
    except CoPresenterUnavailableError:
        raise _person_access_failed() from None
    if isinstance(groups, AccessRefusal):
        raise _person_access_failed()
    return groups


def _person_access_failed() -> TurnStopped:
    return TurnStopped(
        TurnStopKind.FAILED, FailureCode.PROVIDER_UNAVAILABLE, detail=_ACCESS_DETAIL
    )


def voice_loop_talks(
    config: AgentSettings,
    relying_party: OidcRelyingParty,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> VoiceLoopTalks:
    """The talks over the gateway the configuration names."""
    return VoiceLoopTalks(
        GatewayBackend(config, tools=[SHOW_SLIDE], transport=transport),
        relying_party,
    )
