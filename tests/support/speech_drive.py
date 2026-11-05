"""Asks and hearings driven through the real speech adapters and the fake service."""

from __future__ import annotations

import base64
import re
import sqlite3
from contextlib import closing
from dataclasses import replace
from typing import TYPE_CHECKING, Final

from presentator.adapters.speech import SpeechHearing, SpeechSpeaking
from tests.support.copresenter_support import (
    ACCESS,
    HEARING,
    REFRESH,
    ROTATED,
    TALK_ORIGIN,
    Run,
    Scenario,
    drive,
    what_the_ask_must_not_show,
)
from tests.support.leak_support import recording_every_logger
from tests.support.speech_service import (
    A_SPEECH_ORIGIN,
    A_SPEECH_VOICE,
    FakeSpeech,
)

if TYPE_CHECKING:
    import logging
    from pathlib import Path

    from tests.support.talk_support import Hosts

WORKLOAD: Final = "workload-access-MARKER"
SAID: Final = "heard-word-MARKER"
THE_SPEECH_MARKERS: Final = (WORKLOAD, "Bearer ", "eyJ", SAID)


def an_issuer_with_a_workload_token(hosts: Hosts) -> None:
    hosts.issuer.issue_workload_access_token(WORKLOAD)


def drive_through_speech(
    tmp_path: Path, fake: FakeSpeech, scenario: Scenario | None = None
) -> Run:
    """Sign an admin in, then ask once, speaking through the real adapters."""
    chosen = scenario or Scenario()
    own_arrangement = chosen.arrange

    def arrange(hosts: Hosts) -> None:
        an_issuer_with_a_workload_token(hosts)
        own_arrangement(hosts)

    return drive(
        tmp_path,
        replace(chosen, arrange=arrange),
        speech=(
            SpeechHearing(A_SPEECH_ORIGIN, A_SPEECH_VOICE, transports=fake.transports),
            SpeechSpeaking(A_SPEECH_ORIGIN, A_SPEECH_VOICE, transports=fake.transports),
        ),
    )


def what_speech_must_not_show(run: Run) -> tuple[str, ...]:
    """Everything an ask must not show, and the tokens and words speech adds."""
    return (*what_the_ask_must_not_show(run), *THE_SPEECH_MARKERS)


def the_audio_of(run: Run) -> list[bytes]:
    """The WAVs the browser got in the answer's `audio` events, in order."""
    encoded = re.findall(r'"wav_b64": "([^"]+)"', run.response.text)
    return [base64.b64decode(wav) for wav in encoded]


def hear_through(
    run: Run, *frames: bytes, frames_back: int = 1
) -> tuple[list[object], list[logging.LogRecord]]:
    """Open the browser's hearing socket; the JSON it received and every log record."""
    received: list[object] = []
    with (
        recording_every_logger() as logs,
        run.talk.websocket_connect(HEARING, headers={"origin": TALK_ORIGIN}) as socket,
    ):
        for frame in frames:
            socket.send_bytes(frame)
        received.extend(socket.receive_json() for _ in range(frames_back))
    return received, logs.records


def stored_session_rows_but_the_refresh_token(run: Run) -> str:
    """Every stored session column the refresh token does not own, as text."""
    with closing(sqlite3.connect(run.hosts.database)) as connection:
        rows = connection.execute(
            "SELECT id, user_id, created_at, expires_at, id_token FROM sessions"
        ).fetchall()
    return repr(rows)


def what_a_stored_session_must_not_hold() -> tuple[str, ...]:
    """The tokens and words of a speech call; the refresh token has its own column."""
    return (ACCESS, REFRESH, ROTATED, WORKLOAD, SAID)
