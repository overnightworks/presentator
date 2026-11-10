"""Answers one question through the client's gateway backend, for the person asking."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Final, NoReturn, assert_never, cast

from agents.agent_file import load_agent
from agents.backend import (
    AgentBackend,
    TurnEvent,
    TurnPerson,
    TurnStopped,
)
from agents.backend.gateway import GatewayBackend
from agents.errors import raise_without_chain
from agents.tools import (
    HandTool,
    JsonObject,
    ToolDeclaration,
    ToolEffect,
    ToolOutcome,
)
from agents.turn import (
    AgentRole,
    Principal,
    StopOutcome,
    TurnMessage,
    TurnRole,
)
from agents.turn_events import (
    AssistantTextEvent,
    FinalEvent,
    ToolCallEvent,
    ToolResultEvent,
)

from presentator.contracts.copresenter import (
    AskingPerson,
    CoPresenterUnavailableError,
    NotEnabledError,
    ShowSlide,
)
from presentator.contracts.models import Role

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable

    import httpx2
    from webauth.oidc import OidcRelyingParty

    from presentator.agent.settings import AgentSettings

_LOGGER = logging.getLogger(__name__)
COPRESENTER_AGENT_FILE: Final = (
    Path(__file__).parent.parent / "agents" / "copresenter.md"
)
_MILLISECONDS: Final = 1000
_SLIDE_NUMBER: Final = "number"


def _slide_number(arguments: JsonObject) -> int | None:
    """The 1-based slide a call names, or nothing when it names no whole number."""
    number = arguments.get(_SLIDE_NUMBER)
    if isinstance(number, int) and not isinstance(number, bool) and number >= 1:
        return number
    return None


def _show_slide(principal: Principal, arguments: JsonObject) -> Awaitable[ToolOutcome]:
    del principal  # the move rides the asking person's own stream
    if _slide_number(arguments) is None:
        outcome = ToolOutcome(
            "number must be a whole number of at least 1", is_error=True
        )
    else:
        outcome = ToolOutcome("shown", is_error=False)
    # The HandToolHandler port wants an awaitable; Sonar S7503 bans a bare async def.
    return asyncio.sleep(0, outcome)


SHOW_SLIDE: Final = HandTool(
    declaration=ToolDeclaration(
        name="show_slide",
        description="Move the shown deck to the slide with this number.",
        parameters={
            "type": "object",
            "properties": {
                _SLIDE_NUMBER: {
                    "type": "integer",
                    "minimum": 1,
                    "description": "The 1-based number of the slide to show.",
                }
            },
            "required": [_SLIDE_NUMBER],
            "additionalProperties": False,
        },
        effect=ToolEffect.READ_ONLY,
    ),
    handler=_show_slide,
)


def _unavailable() -> NoReturn:
    raise_without_chain(CoPresenterUnavailableError())


class _RunSummary:
    """What a run showed, without a word of its content."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._started = clock()
        self._events: dict[str, int] = {}
        self._first_move_ms: int | None = None

    def note_move(self) -> None:
        if self._first_move_ms is None:
            self._first_move_ms = self._elapsed_ms()

    def note(self, event: object) -> None:
        name = type(event).__name__
        self._events[name] = self._events.get(name, 0) + 1

    @property
    def has_events(self) -> bool:
        return bool(self._events)

    def log(self) -> None:
        moved = "" if self._first_move_ms is None else f" move_ms={self._first_move_ms}"
        _LOGGER.info(
            "gateway run summary: events=%s end_ms=%d%s",
            ",".join(f"{name}:{count}" for name, count in self._events.items()),
            self._elapsed_ms(),
            moved,
        )

    def _elapsed_ms(self) -> int:
        return round((self._clock() - self._started) * _MILLISECONDS)


class GatewayAnswerer:
    """Answers a question with the co-presenter agent's turn on the gateway."""

    def __init__(
        self,
        backend: AgentBackend,
        relying_party: OidcRelyingParty,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Run turns on this backend, for this relying party's people."""
        self._backend = backend
        self._relying_party = relying_party
        self._monotonic = monotonic
        self._agent = load_agent(COPRESENTER_AGENT_FILE, [SHOW_SLIDE.declaration])

    @asynccontextmanager
    async def answer(
        self, *, message: str, person: AskingPerson
    ) -> AsyncGenerator[AsyncIterator[str | ShowSlide]]:
        """Stream the turn's text and slide moves; a refusal or failure is one error."""
        try:
            request = self._agent.request(
                [TurnMessage(role=TurnRole.USER, text=message)]
            )
        except ValueError:
            _log_closed(failure="request_invalid")
            _unavailable()
        summary = _RunSummary(self._monotonic)
        async with aclosing(
            self._backend.run(request, self._turn_person(person))
        ) as events:
            answered = _answered(events, summary)
            try:
                yield answered
            finally:
                await answered.aclose()
                if summary.has_events:
                    summary.log()

    def _turn_person(self, person: AskingPerson) -> TurnPerson:
        # webauth binds the store to one issuer and an account's id is that issuer's
        # `sub`. Two spellings of the issuer would split one person's budget, so it
        # goes out exactly as the relying party holds it.
        principal = Principal(iss=self._relying_party.issuer, sub=person.subject)
        role: AgentRole = "admin" if person.role is Role.ADMIN else "user"

        async def groups() -> frozenset[str]:  # NOSONAR(S7503) the client awaits it
            return person.groups

        return TurnPerson(principal, role, groups)


async def _answered(
    events: AsyncIterator[TurnEvent], summary: _RunSummary
) -> AsyncGenerator[str | ShowSlide]:
    asked_slides: dict[str, int] = {}
    try:
        async for event in events:
            summary.note(event)
            match event:
                case AssistantTextEvent(text=text):
                    yield text
                case ToolCallEvent(
                    tool_use_id=call_id, name=SHOW_SLIDE.declaration.name
                ) if (
                    # the client types the call's input as a bare dict
                    number := _slide_number(cast("JsonObject", event.input))  # pyright: ignore[reportUnknownMemberType]
                ) is not None:
                    asked_slides[call_id] = number
                case ToolCallEvent():
                    continue
                case ToolResultEvent(tool_use_id=call_id, is_error=False) if (
                    call_id in asked_slides
                ):
                    summary.note_move()
                    yield ShowSlide(number=asked_slides[call_id])
                case ToolResultEvent():
                    continue
                case FinalEvent():
                    return
                case _:
                    assert_never(event)
    except TurnStopped as stopped:
        _refuse(stopped)


def _refuse(stopped: TurnStopped) -> NoReturn:
    _log_closed(
        kind=stopped.kind.value,
        code=stopped.code.value,
        outcome=stopped.outcome.value,
        detail=stopped.detail,
        retry_after_s=None
        if stopped.retry_after_s is None
        else str(stopped.retry_after_s),
    )
    match stopped.outcome:
        case StopOutcome.NOT_ENABLED:
            raise_without_chain(NotEnabledError())
        case StopOutcome.BUSY | StopOutcome.BUDGET_SPENT | StopOutcome.UNAVAILABLE:
            _unavailable()
        case _:
            assert_never(stopped.outcome)


def _log_closed(**values: str | None) -> None:
    # Only closed values: a body, a message or an exception may carry the question.
    shown = " ".join(
        f"{name}={value}" for name, value in values.items() if value is not None
    )
    _LOGGER.warning("gateway turn refused: %s", shown)


def gateway_answerer(
    config: AgentSettings,
    relying_party: OidcRelyingParty,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> GatewayAnswerer:
    """The answerer over the gateway the configuration names."""
    return GatewayAnswerer(
        GatewayBackend(config, tools=[SHOW_SLIDE], transport=transport), relying_party
    )
