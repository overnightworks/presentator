"""The speech tokens a session yields: one refresh grant, one workload grant."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from presentator.adapters.session_access import SessionAccess
from presentator.contracts.copresenter import AccessRefusal, SpeechTokens
from tests.support.lobby import A_SPEECH_SCOPE
from tests.support.sign_in_support import Scene, a_scene

if TYPE_CHECKING:
    from pathlib import Path

REFRESH = "refresh-MARKER"
ROTATED = "rotated-MARKER"
ACCESS = "access-MARKER"
LOGGER = "presentator.adapters.session_access"


class Arranged:
    """A signed-in session over a fake issuer and the access reader on it."""

    def __init__(self, scene: Scene) -> None:
        """Sign a person in; their one session row is the one asked about."""
        scene.issuer.issue_refresh_token(REFRESH)
        scene.issuer.issue_person_access_token(ACCESS)
        scene.sign_in(groups=["/apps/presentator"])
        (self.session_id,) = scene.stored_sessions()
        self.scene = scene
        self.reader = SessionAccess(
            scene.relying_party,
            scene.sessions,
            scene.config,
            speech_scope=A_SPEECH_SCOPE,
        )

    def read(self) -> SpeechTokens | AccessRefusal:
        return asyncio.run(self.reader.speech_tokens_of(self.session_id))


@pytest.fixture
def arranged(tmp_path: Path) -> Arranged:
    return Arranged(a_scene(tmp_path))


def test_a_session_yields_the_persons_token_and_the_workload_token(
    arranged: Arranged,
) -> None:
    issued = "the-issued-workload-token"
    arranged.scene.issuer.workload_token = issued

    tokens = arranged.read()

    assert tokens == SpeechTokens(person=ACCESS, workload=issued)


@pytest.mark.parametrize("scope", ["a-first-scope", "another-scope"])
def test_the_workload_grant_asks_for_exactly_the_configured_scope(
    arranged: Arranged, scope: str
) -> None:
    scene = arranged.scene
    reader = SessionAccess(
        scene.relying_party, scene.sessions, scene.config, speech_scope=scope
    )

    asyncio.run(reader.speech_tokens_of(arranged.session_id))

    assert scene.issuer.workload_scopes == [scope]


@pytest.mark.parametrize("error", ["invalid_scope", "invalid_client"])
def test_a_refused_workload_grant_is_unavailable_with_a_closed_warning(
    arranged: Arranged, error: str, caplog: pytest.LogCaptureFixture
) -> None:
    arranged.scene.issuer.workload_refusal = (error, "description-MARKER")
    caplog.set_level(logging.DEBUG)

    assert arranged.read() is AccessRefusal.ISSUER_UNAVAILABLE

    (warning,) = [
        record.getMessage() for record in caplog.records if record.name == LOGGER
    ]
    assert warning.endswith("token-request-refused")
    assert "description-MARKER" not in caplog.text


def test_a_refused_refresh_makes_no_workload_grant(arranged: Arranged) -> None:
    arranged.scene.issuer.revoke_refresh_token(REFRESH)
    before = len(arranged.scene.issuer.grant_types)

    assert arranged.read() is AccessRefusal.SIGN_IN_AGAIN

    assert arranged.scene.issuer.grant_types[before:] == ["refresh_token"]
    assert arranged.scene.issuer.workload_scopes == []
