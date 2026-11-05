"""What the issuer grants a signed-in session now: its groups and the speech tokens."""

from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
from typing import TYPE_CHECKING, assert_never

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


class SessionAccess:
    """Reads the groups and the speech tokens of a session, each by the issuer."""

    def __init__(
        self,
        relying_party: OidcRelyingParty,
        sessions: SessionRecordStore,
        config: WebAuthConfig,
        *,
        speech_scope: str,
    ) -> None:
        """Renew through this client, read and rotate in these sessions.

        The workload grant asks for `speech_scope`, so its token names the speech
        service as its audience.
        """
        self._relying_party = relying_party
        self._sessions = sessions
        self._config = config
        self._speech_scope = speech_scope

    async def groups_of(self, session_id: str) -> frozenset[str] | AccessRefusal:
        """The session's person's groups, or why it has none."""
        groups = await _in_a_thread(
            lambda: current_groups(
                self._relying_party,
                session_id,
                session_transaction=self._transaction,
                config=self._config,
            ),
            reading="the groups",
        )
        if isinstance(groups, AccessTokenRefusal):
            return _refusal_of(groups)
        return frozenset(groups)

    async def speech_tokens_of(self, session_id: str) -> SpeechTokens | AccessRefusal:
        """The person's fresh access token and presentator's own, for speech."""
        person = await _in_a_thread(
            lambda: fresh_access_token(
                self._relying_party,
                session_id,
                session_transaction=self._transaction,
                config=self._config,
            ),
            reading="the person's speech token",
        )
        if isinstance(person, AccessTokenRefusal):
            return _refusal_of(person)
        workload = await _in_a_thread(
            lambda: self._relying_party.workload_access_token(
                scopes=(self._speech_scope,)
            ),
            reading="the workload speech token",
        )
        if isinstance(workload, OidcRefusal):
            return _issuer_gave_nothing(workload)
        return SpeechTokens(
            person=person.get_secret_value(), workload=workload.get_secret_value()
        )

    def _transaction(self) -> nullcontext[SessionRecordStore]:
        return nullcontext(self._sessions)


async def _in_a_thread[Result](read: Callable[[], Result], *, reading: str) -> Result:
    # A cancelled ask cancels the await, not the worker: it still stores the
    # rotated refresh token, and no later step runs.
    try:
        return await asyncio.to_thread(read)
    # Every thread failure is one unavailable error; its text may hold a token.
    except Exception as error:  # noqa: BLE001
        _LOGGER.warning("reading %s failed: %s", reading, type(error).__name__)
    # Raised outside the handler, so the error carries neither a cause nor a
    # context.
    raise CoPresenterUnavailableError


def _refusal_of(refusal: AccessTokenRefusal) -> AccessRefusal:
    match refusal.reason:
        case AccessTokenRefusalReason.SIGN_IN_AGAIN:
            return AccessRefusal.SIGN_IN_AGAIN
        case AccessTokenRefusalReason.SESSION_ENDED:
            return AccessRefusal.SESSION_ENDED
        case AccessTokenRefusalReason.ISSUER_UNAVAILABLE:
            return _issuer_gave_nothing(refusal.issuer_refusal)
        case _:
            assert_never(refusal.reason)


def _issuer_gave_nothing(refusal: OidcRefusal | None) -> AccessRefusal:
    # Only the closed reason and the issuer's error code are shown.
    _LOGGER.warning(
        "issuer gave nothing: %s %s",
        None if refusal is None else refusal.reason.value,
        None if refusal is None else refusal.idp_error,
    )
    return AccessRefusal.ISSUER_UNAVAILABLE
