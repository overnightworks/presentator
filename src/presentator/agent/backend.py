"""Answers one question through the client's gateway backend, for the person asking."""

from __future__ import annotations

import logging
import time
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Final, NoReturn, assert_never

from agent_runtime_client.agents import load_agent
from agent_runtime_client.backend import (
    AgentBackend,
    TurnEvent,
    TurnPerson,
    TurnStopKind,
    TurnStopped,
)
from agent_runtime_client.backend.gateway import GatewayBackend
from agent_runtime_client.errors import raise_without_chain
from agent_runtime_client.turn import (
    AgentRole,
    Principal,
    RejectionCode,
    TurnMessage,
    TurnRole,
)
from agent_runtime_client.turn_events import (
    AssistantTextEvent,
    FinalEvent,
    ToolCallEvent,
    ToolResultEvent,
)

from presentator.contracts.copresenter import (
    AskingPerson,
    CoPresenterUnavailableError,
    NotEnabledError,
)
from presentator.contracts.models import Role

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable

    import httpx2
    from webauth.oidc import OidcRelyingParty

    from presentator.agent.settings import AgentSettings

_LOGGER = logging.getLogger(__name__)
COPRESENTER_AGENT_FILE: Final = (
    Path(__file__).parent.parent / "agents" / "copresenter.md"
)
_MILLISECONDS: Final = 1000


def _unavailable() -> NoReturn:
    raise_without_chain(CoPresenterUnavailableError())


class _RunSummary:
    """What a run showed, without a word of its content."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._started = clock()
        self._events: dict[str, int] = {}

    def note(self, event: object) -> None:
        name = type(event).__name__
        self._events[name] = self._events.get(name, 0) + 1

    @property
    def has_events(self) -> bool:
        return bool(self._events)

    def log(self) -> None:
        elapsed = round((self._clock() - self._started) * _MILLISECONDS)
        _LOGGER.info(
            "gateway run summary: events=%s end_ms=%d",
            ",".join(f"{name}:{count}" for name, count in self._events.items()),
            elapsed,
        )


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
        self._agent = load_agent(COPRESENTER_AGENT_FILE, [])

    @asynccontextmanager
    async def answer(
        self, *, message: str, person: AskingPerson
    ) -> AsyncGenerator[AsyncIterator[str]]:
        """Stream the turn's text; any refusal or failure is one unavailable error."""
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
            texts = _texts(events, summary)
            try:
                yield texts
            finally:
                await texts.aclose()
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


async def _texts(
    events: AsyncIterator[TurnEvent], summary: _RunSummary
) -> AsyncGenerator[str]:
    try:
        async for event in events:
            summary.note(event)
            match event:
                case AssistantTextEvent(text=text):
                    yield text
                case ToolCallEvent() | ToolResultEvent():
                    continue
                case FinalEvent():
                    return
                case _:
                    assert_never(event)
    except TurnStopped as stopped:
        _refuse(stopped)


def _refuse(stopped: TurnStopped) -> NoReturn:
    code = stopped.code
    _log_closed(
        kind=stopped.kind.value,
        code=None if code is None else code.value,
        detail=stopped.detail,
        retry_after_s=None
        if stopped.retry_after_s is None
        else str(stopped.retry_after_s),
    )
    if stopped.kind is TurnStopKind.REFUSED and code is RejectionCode.NOT_ENABLED:
        raise_without_chain(NotEnabledError())
    _unavailable()


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
    return GatewayAnswerer(GatewayBackend(config, transport=transport), relying_party)
