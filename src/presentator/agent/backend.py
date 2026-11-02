"""The co-presenter's agent backend: agent-runtime's connector behind our ports."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Final, NoReturn, assert_never

from agent_runtime_client.agents import load_agent
from agent_runtime_client.backend import (
    AgentBackend,
    Asker,
    RunTokens,
    TurnEvent,
    TurnStopKind,
    TurnStopped,
)
from agent_runtime_client.backend.service import ServiceBackend
from agent_runtime_client.capability import CapabilityPrincipal
from agent_runtime_client.errors import raise_without_chain
from agent_runtime_client.turn_events import AssistantTextEvent, FinalEvent
from agent_runtime_client.wire import RejectionCode, RunRequest, TurnMessage, TurnRole
from pydantic import SecretStr
from webauth.oidc import OidcRefusal
from webauth.oidc_login import (
    AccessTokenRefusal,
    AccessTokenRefusalReason,
    fresh_access_token,
)

from presentator.contracts.copresenter import (
    AccessRefusal,
    CoPresenterUnavailableError,
    NotEnabledError,
    PersonToken,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable

    from agent_runtime_client.config import AgentConfig
    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore

    from presentator.ports.copresenter import TokenSource

_LOGGER = logging.getLogger(__name__)
_AGENT_FILE: Final = Path(__file__).parent.parent / "agents" / "copresenter.md"
_MILLISECONDS: Final = 1000


def _fail(reason: str) -> NoReturn:
    _LOGGER.warning("agent service run failed: %s", reason)
    raise_without_chain(CoPresenterUnavailableError())


def _raise_stop(stopped: TurnStopped) -> NoReturn:
    # Every field is the client's closed vocabulary, never text from the wire.
    code = None if stopped.code is None else stopped.code.value
    _LOGGER.warning(
        "agent service run stopped: %s %s retry_after=%s detail=%s",
        stopped.kind.value,
        code,
        stopped.retry_after_s,
        stopped.detail,
    )
    if stopped.code is RejectionCode.NOT_ENABLED:
        raise_without_chain(NotEnabledError())
    raise_without_chain(CoPresenterUnavailableError())


def _milliseconds(seconds: float) -> int:
    return round(seconds * _MILLISECONDS)


async def _bearer_token(source: TokenSource) -> str:
    # A source's exception is foreign and may hold the token, so it is never
    # raised, logged or chained. Cancellation and process control pass through.
    try:
        return await source()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit) as error:
        error.__cause__ = error.__context__ = None
        raise
    except BaseException as error:  # noqa: BLE001 - the boundary sanitises every other supplier failure; gather routes measured worse
        _fail(f"token source raised {type(error).__name__}")


@dataclass
class _RunTally:
    """What an opened run showed, without a word of its content."""

    clock: Callable[[], float]
    started: float | None = None
    events: dict[str, int] = field(default_factory=dict[str, int])
    first_text: float | None = None
    refused: bool = False
    stop: str | None = None

    def open(self) -> None:
        self.started = self.clock()

    def note(self, event: TurnEvent) -> None:
        name = type(event).__name__
        self.events[name] = self.events.get(name, 0) + 1
        if isinstance(event, AssistantTextEvent) and self.first_text is None:
            self.first_text = self.clock()

    def note_stop(self, stopped: TurnStopped) -> None:
        self.refused = stopped.kind is TurnStopKind.REFUSED
        self.stop = stopped.detail or stopped.kind.value

    def log_summary(self) -> None:
        started = self.started
        if started is None or self.refused:
            return
        ended = self.clock()
        first_text = self.first_text
        _LOGGER.info(
            "agent service run summary: events=%s stop=%s first_text_ms=%s end_ms=%s",
            ",".join(f"{name}:{count}" for name, count in self.events.items()),
            self.stop,
            None if first_text is None else _milliseconds(first_text - started),
            _milliseconds(ended - started),
        )


async def _texts(
    events: AsyncGenerator[TurnEvent], tally: _RunTally
) -> AsyncGenerator[str]:
    try:
        async for event in events:
            tally.note(event)
            if isinstance(event, AssistantTextEvent):
                yield event.text
            elif isinstance(event, FinalEvent) and _has_no_words(event, tally):
                _fail("succeeded with files and no words")
    except TurnStopped as stopped:
        tally.note_stop(stopped)
        _raise_stop(stopped)
    finally:
        await events.aclose()


def _has_no_words(final: FinalEvent, tally: _RunTally) -> bool:
    return not final.text and tally.first_text is None


class ServiceAnswerer:
    """Answer one question with a run of the agent service's co-presenter agent."""

    def __init__(
        self,
        *,
        config: AgentConfig,
        issuer: str,
        workload_token: TokenSource,
        monotonic: Callable[[], float] = time.monotonic,
        backend_factory: Callable[[AgentConfig], AgentBackend] = ServiceBackend,
    ) -> None:
        """Reach the service this configuration names, as this workload.

        `issuer` is the relying party's, which names every account that asks.
        """
        self._backend = backend_factory(config)
        self._issuer = issuer
        self._workload_token = workload_token
        self._monotonic = monotonic
        self._agent = load_agent(_AGENT_FILE, [])

    @asynccontextmanager
    async def answer(
        self, *, message: str, subject: str, person_token: TokenSource
    ) -> AsyncGenerator[AsyncIterator[str]]:
        """Stream the run's text; any refusal or failure is one unavailable error.

        The run opens at the first read, so a refusal surfaces there.
        """
        request = self._run_request(message)
        tally = _RunTally(self._monotonic)
        asker = Asker(
            principal=CapabilityPrincipal(iss=self._issuer, sub=subject),
            tokens=partial(self._tokens, person_token, tally),
        )
        texts = _texts(self._backend.run(request, asker), tally)
        try:
            yield texts
        finally:
            await texts.aclose()
            tally.log_summary()

    async def _tokens(self, person_token: TokenSource, tally: _RunTally) -> RunTokens:
        person = await _bearer_token(person_token)
        workload = await _bearer_token(self._workload_token)
        tally.open()
        return RunTokens(person=SecretStr(person), workload=SecretStr(workload))

    def _run_request(self, message: str) -> RunRequest:
        try:
            return self._agent.request([TurnMessage(role=TurnRole.USER, text=message)])
        except ValueError as error:
            _fail(f"request invalid: {type(error).__name__}")


def _unavailable() -> NoReturn:
    raise_without_chain(CoPresenterUnavailableError())


def _thread_failed(error: Exception) -> NoReturn:
    # The error is foreign and may hold a token, so only its type is shown.
    _LOGGER.warning("agent token source failed: %s", type(error).__name__)
    _unavailable()


class PersonAccessTokens:
    """A signed-in person's fresh access token, from their session."""

    def __init__(
        self,
        relying_party: OidcRelyingParty,
        sessions: SessionRecordStore,
        config: WebAuthConfig,
    ) -> None:
        """Renew through this client, read and rotate in these sessions."""
        self._relying_party = relying_party
        self._sessions = sessions
        self._config = config

    async def fresh(self, session_id: str) -> PersonToken | AccessRefusal:
        """The session's person's token, or why it has none."""
        try:
            token = await asyncio.to_thread(
                fresh_access_token,
                self._relying_party,
                session_id,
                session_transaction=lambda: nullcontext(self._sessions),
                config=self._config,
            )
        # Every thread failure is one unavailable error.
        except Exception as error:  # noqa: BLE001
            _thread_failed(error)
        if isinstance(token, AccessTokenRefusal):
            return _refusal_of(token)
        return PersonToken(token.get_secret_value())


def _refusal_of(refusal: AccessTokenRefusal) -> AccessRefusal:
    match refusal.reason:
        case AccessTokenRefusalReason.SIGN_IN_AGAIN:
            return AccessRefusal.SIGN_IN_AGAIN
        case AccessTokenRefusalReason.SESSION_ENDED:
            return AccessRefusal.SESSION_ENDED
        case AccessTokenRefusalReason.ISSUER_UNAVAILABLE:
            _LOGGER.warning("issuer gave no access token: %s", refusal.issuer_refusal)
            return AccessRefusal.ISSUER_UNAVAILABLE
        case _:
            assert_never(refusal.reason)


class WorkloadToken:
    """Presentator's own access token, fetched anew on every call."""

    def __init__(
        self, relying_party: OidcRelyingParty, scopes: tuple[str, ...]
    ) -> None:
        """Fetch through this client, asking for these scopes."""
        self._relying_party = relying_party
        self._scopes = scopes

    async def __call__(self) -> str:
        """A workload access token; any refusal or failure is one unavailable error."""
        try:
            token = await asyncio.to_thread(
                self._relying_party.workload_access_token, scopes=self._scopes
            )
        # Every thread failure is one unavailable error.
        except Exception as error:  # noqa: BLE001
            _thread_failed(error)
        if isinstance(token, OidcRefusal):
            _LOGGER.warning("issuer gave no workload token: %s", token)
            _unavailable()
        return token.get_secret_value()
