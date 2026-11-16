"""What the issuer grants a signed-in session now: its groups and its speech tokens."""

from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
from enum import StrEnum
from typing import TYPE_CHECKING, assert_never

from logline import StepTimer
from webauth.oidc import OidcRefusal
from webauth.oidc_login import (
    AccessTokenRefusal,
    AccessTokenRefusalReason,
    current_groups,
    fresh_access_token,
)

from presentator.contracts.copresenter import (
    AccessRefusal,
    CoPresenterUnavailableError,
    SpeechTokens,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore

_LOGGER = logging.getLogger(__name__)


class TokenPurpose(StrEnum):
    """What an `access_token.ended` line read the token for."""

    GROUPS = "groups"
    SPEECH_TOKENS = "speech_tokens"


class TokenFailure(StrEnum):
    """Why an `access_token.ended` line is failed without a refusal's own code."""

    READ_FAILED = "read_failed"
    ISSUER_UNAVAILABLE = "issuer_unavailable"


class SessionAccess:
    """Reads what a session may use now, each read by its own grants at the issuer."""

    def __init__(
        self,
        relying_party: OidcRelyingParty,
        sessions: SessionRecordStore,
        config: WebAuthConfig,
        *,
        speech_scope: str,
    ) -> None:
        """Renew through this client, read and rotate in these sessions."""
        self._relying_party = relying_party
        self._sessions = sessions
        self._config = config
        self._speech_scope = speech_scope

    async def groups_of(self, session_id: str) -> frozenset[str] | AccessRefusal:
        """The session's person's groups, or why it has none."""
        with StepTimer(
            "access_token.ended", _LOGGER, purpose=TokenPurpose.GROUPS
        ) as step:
            groups = await _in_a_worker(lambda: self._read_groups(session_id), step)
            if isinstance(groups, AccessTokenRefusal):
                return _refusal_of(groups, step)
        return frozenset(groups)

    async def speech_tokens_of(self, session_id: str) -> SpeechTokens | AccessRefusal:
        """The person's token and this instance's own, or why there are none."""
        with StepTimer(
            "access_token.ended", _LOGGER, purpose=TokenPurpose.SPEECH_TOKENS
        ) as step:
            granted = await _in_a_worker(
                lambda: self._read_speech_tokens(session_id), step
            )
            if isinstance(granted, AccessTokenRefusal):
                return _refusal_of(granted, step)
            if isinstance(granted, OidcRefusal):
                return _issuer_unavailable(granted, step)
        return granted

    def _read_groups(self, session_id: str) -> tuple[str, ...] | AccessTokenRefusal:
        return current_groups(
            self._relying_party,
            session_id,
            session_transaction=lambda: nullcontext(self._sessions),
            config=self._config,
        )

    def _read_speech_tokens(
        self, session_id: str
    ) -> SpeechTokens | AccessTokenRefusal | OidcRefusal:
        person = fresh_access_token(
            self._relying_party,
            session_id,
            session_transaction=lambda: nullcontext(self._sessions),
            config=self._config,
        )
        if isinstance(person, AccessTokenRefusal):
            return person
        workload = self._relying_party.workload_access_token(
            scopes=(self._speech_scope,)
        )
        if isinstance(workload, OidcRefusal):
            return workload
        return SpeechTokens(
            person=person.get_secret_value(), workload=workload.get_secret_value()
        )


async def _in_a_worker[Granted](
    read: Callable[[], Granted], step: StepTimer
) -> Granted:
    # A cancelled caller cancels the await, not the worker: it still stores the
    # rotated refresh token, and no later step runs.
    failure: str | None = None
    try:
        return await asyncio.to_thread(read)
    # Every thread failure is one unavailable error; its text may hold a token.
    except Exception as error:  # noqa: BLE001
        failure = type(error).__name__
    step.fail(TokenFailure.READ_FAILED)
    step.add(cause=failure)
    # Raised outside the handler, so the error it replaces is not its context.
    raise CoPresenterUnavailableError


def _refusal_of(refusal: AccessTokenRefusal, step: StepTimer) -> AccessRefusal:
    match refusal.reason:
        case AccessTokenRefusalReason.SIGN_IN_AGAIN:
            step.refuse(refusal.reason.value)
            return AccessRefusal.SIGN_IN_AGAIN
        case AccessTokenRefusalReason.SESSION_ENDED:
            step.refuse(refusal.reason.value)
            return AccessRefusal.SESSION_ENDED
        case AccessTokenRefusalReason.ISSUER_UNAVAILABLE:
            return _issuer_unavailable(refusal.issuer_refusal, step)
        case _:
            assert_never(refusal.reason)


def _issuer_unavailable(issuer: OidcRefusal | None, step: StepTimer) -> AccessRefusal:
    step.fail(TokenFailure.ISSUER_UNAVAILABLE)
    if issuer is not None:
        step.add(issuer_reason=issuer.reason.value)
    return AccessRefusal.ISSUER_UNAVAILABLE
