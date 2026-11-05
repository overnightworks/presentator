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
    HEARING,
    REFRESH,
    ROTATED,
    TALK_ORIGIN,
    THE_GOOD_ANSWER,
    Run,
    Scenario,
    accounts_of,
    drive,
    what_the_ask_must_not_show,
)
from tests.support.leak_support import everything_shown, recording_every_logger
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
THE_SPEECH_MARKERS: Final = (
    WORKLOAD,
    "Bearer ",
    "eyJ",
    SAID,
    *THE_GOOD_ANSWER,
)


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


def stored_session_markers(run: Run, markers: tuple[str, ...]) -> list[str]:
    """The markers in a stored session row, column by column.

    Only the refresh token's own column may hold the refresh tokens, and the user
    column the row id of the account it belongs to.
    """
    own_markers = {
        "refresh_token": {REFRESH, ROTATED},
        "user_id": set(accounts_of(run.hosts)),
    }
    with closing(sqlite3.connect(run.hosts.database)) as connection:
        cursor = connection.execute("SELECT * FROM sessions")
        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchall()
    return [
        marker
        for row in rows
        for column, value in zip(columns, row, strict=True)
        for marker in markers
        if marker in str(value) and marker not in own_markers.get(column, set())
    ]


def the_ask_beyond_its_answer(run: Run) -> str:
    """What the browser got of an ask, less the answer's own words in its answer events.

    The answer is the product: its sentences travel in every event of the answer,
    and in no `error` event, header or other byte of the response.
    """
    shown = [
        block if block.startswith("event: error\n") else _without_the_answer(block)
        for block in run.response.text.split("\n\n")
    ]
    return "\n\n".join([str(run.response.headers), *shown])


def _without_the_answer(block: str) -> str:
    for sentence in THE_GOOD_ANSWER:
        block = block.replace(sentence, "")
    return block


def leaks_of(run: Run, *, records: list[logging.LogRecord], browser: str) -> list[str]:
    """Every L1 marker found on a surface it must not reach, with the surface."""
    markers = what_speech_must_not_show(run)
    logged = everything_shown(records, run.errors)
    return [
        *[f"{marker!r} in a log or an error" for marker in markers if marker in logged],
        *[f"{marker!r} in the browser" for marker in markers if marker in browser],
        *[
            f"{marker!r} in a stored session"
            for marker in stored_session_markers(run, markers)
        ],
    ]
