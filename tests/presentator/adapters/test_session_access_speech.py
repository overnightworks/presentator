"""The speech tokens of a session: both grants, their refusals and cancellation."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from presentator.contracts.copresenter import (
    AccessRefusal,
    CoPresenterUnavailableError,
    SpeechTokens,
)
from tests.presentator.adapters.support.session_access_support import (
    REFRESH,
    ROTATED,
    Arranged,
    LoadFails,
    RecordingExecutor,
)
from tests.support.speech_service import A_SPEECH_SCOPE

if TYPE_CHECKING:
    from presentator.adapters.session_access import SessionAccess

PERSON = "person-access-MARKER"
WORKLOAD = "workload-access-MARKER"


def tokens_of(arranged: Arranged) -> SessionAccess:
    arranged.scene.issuer.issue_person_access_token(PERSON)
    arranged.scene.issuer.issue_workload_access_token(WORKLOAD)
    return arranged.reader


def test_a_signed_in_session_gets_the_persons_token_and_the_apps_own(
    arranged: Arranged,
) -> None:
    """G2, G3: one refresh grant and one client-credentials grant, for the scope."""
    reader = tokens_of(arranged)
    before = len(arranged.scene.issuer.grant_types)

    tokens = asyncio.run(reader.speech_tokens_of(arranged.session_id))

    assert tokens == SpeechTokens(person=PERSON, workload=WORKLOAD)
    assert arranged.scene.issuer.grant_types[before:] == [
        "refresh_token",
        "client_credentials",
    ]
    assert arranged.scene.issuer.workload_scopes == [A_SPEECH_SCOPE]


def test_a_revoked_refresh_token_asks_for_a_new_sign_in_and_asks_for_no_workload_token(
    arranged: Arranged,
) -> None:
    """G3, G4."""
    reader = tokens_of(arranged)
    arranged.scene.issuer.revoke_refresh_token(REFRESH)

    refusal = asyncio.run(reader.speech_tokens_of(arranged.session_id))

    assert refusal is AccessRefusal.SIGN_IN_AGAIN
    assert arranged.scene.issuer.workload_scopes == []


@pytest.mark.parametrize(
    "refusal", ["workload_is_refused", "workload_scope_is_invalid"]
)
def test_a_refused_workload_grant_is_an_unavailable_issuer_logged_by_its_reason(
    arranged: Arranged, caplog: pytest.LogCaptureFixture, refusal: str
) -> None:
    """G2: the closed reason and the issuer's error code, nothing else."""
    reader = tokens_of(arranged)
    setattr(arranged.scene.issuer, refusal, True)
    arranged.scene.issuer.refusal_description = "described-MARKER"
    caplog.set_level(logging.DEBUG)

    refused = asyncio.run(reader.speech_tokens_of(arranged.session_id))

    assert refused is AccessRefusal.ISSUER_UNAVAILABLE
    assert "described-MARKER" not in caplog.text
    assert "invalid_" in caplog.text


@pytest.mark.parametrize("failing", ["person", "workload"])
def test_a_thread_that_raises_is_one_unavailable_error_with_no_chain(
    arranged: Arranged, caplog: pytest.LogCaptureFixture, failing: str
) -> None:
    """G4: neither `__cause__` nor `__context__`, and the text shows no token."""
    reader = tokens_of(arranged)
    scene = arranged.scene
    if failing == "person":
        reader = type(reader)(
            scene.relying_party,
            LoadFails(scene.database),
            scene.config,
            speech_scope=A_SPEECH_SCOPE,
        )
    else:
        scene.issuer.workload_is_refused = False

        def fail() -> None:
            if scene.issuer.grant_types[-1] == "client_credentials":
                raise RuntimeError(PERSON)

        scene.issuer.during_token_request = fail
    caplog.set_level(logging.DEBUG)

    reading = reader.speech_tokens_of(arranged.session_id)

    with pytest.raises(CoPresenterUnavailableError) as failed:
        asyncio.run(reading)

    assert (failed.value.__cause__, failed.value.__context__) == (None, None)
    assert PERSON not in caplog.text


def test_a_cancelled_speech_read_still_stores_the_rotation_and_leaves_no_chain(
    arranged: Arranged,
) -> None:
    """G4."""
    reader = tokens_of(arranged)
    arranged.scene.issuer.rotate_refresh_token_to(ROTATED)

    async def cancelled_inside_the_first_thread() -> asyncio.CancelledError:
        executor = RecordingExecutor()
        asyncio.get_running_loop().set_default_executor(executor)
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()

        def enter() -> None:
            loop.call_soon_threadsafe(entered.set)

        arranged.scene.issuer.during_token_request = enter
        task = asyncio.create_task(reader.speech_tokens_of(arranged.session_id))
        await entered.wait()
        task.cancel()
        await asyncio.wrap_future(executor.recorded[0])
        try:
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
        finally:
            executor.shutdown(wait=True)
        return caught.value

    error = asyncio.run(cancelled_inside_the_first_thread())

    assert (error.__cause__, error.__context__) == (None, None)
    assert arranged.scene.stored_tokens() == [ROTATED]
    assert arranged.scene.issuer.workload_scopes == []
