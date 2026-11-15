"""How a fake speech service ends a hearing, and what the browser shows of it."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from speech_client import wire
from speech_client.relay import HopClose

from tests.support.speech_service import (
    AN_ITEM,
    PLANTED,
    Hear,
    a_completed,
    a_delta,
    a_failed_item,
    after_the_first_audio,
    an_error_event,
    busy_at_the_upgrade,
    crashing_after_the_first_audio,
    denying,
    draining,
    one_after_another,
    sending,
)


def _marked_frames_the_parser_refuses() -> dict[str, Hear]:
    return {
        "unknown-event": after_the_first_audio(
            json.dumps({"type": "unknown.event", "event_id": PLANTED})
        ),
        "invalid-payload": after_the_first_audio(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.delta",
                    "event_id": PLANTED,
                    "item_id": AN_ITEM,
                    "delta": [PLANTED],
                }
            )
        ),
        "non-json-frame": after_the_first_audio(f"not json at all {PLANTED}"),
    }


@dataclass(frozen=True)
class Ending:
    """How a service ends a hearing, and what the browser shows of it.

    `hear` builds the service's handler anew for each scenario: a handler that
    steps through upgrades keeps that state to one scenario.
    """

    hear: Callable[[], Hear]
    frames: list[dict[str, object]]
    close: HopClose


def _refusing_after(cut: Callable[[], Hear]) -> Callable[[], Hear]:
    """A session cut after its first audio, and an upgrade the service then refuses."""
    return lambda: one_after_another(cut(), denying(503))


HEARING_ENDS: Final[dict[str, Ending]] = {
    "closes-1000": Ending(
        lambda: after_the_first_audio(close_code=1000, close_reason=PLANTED),
        [],
        HopClose.NORMAL,
    ),
    **{
        f"closes-{code}": Ending(
            _refusing_after(
                lambda code=code: after_the_first_audio(
                    close_code=code, close_reason=PLANTED
                )
            ),
            [],
            HopClose.SERVICE_FAILED,
        )
        for code in (1009, 1011, 1013)
    },
    "peer-crashes-mid-session": Ending(
        _refusing_after(crashing_after_the_first_audio),
        [],
        HopClose.SERVICE_FAILED,
    ),
    **{
        name: Ending(lambda hear=hear: hear, [], HopClose.SERVICE_FAILED)
        for name, hear in _marked_frames_the_parser_refuses().items()
    },
}


def _interim(text: str) -> dict[str, object]:
    return {"text": text, "final": False, "language": None}


def _final(text: str, language: str = "de") -> dict[str, object]:
    return {"text": text, "final": True, "language": language}


BROWSER_ENDINGS: dict[str, Ending] = {
    "deltas-grow-and-completed-replaces": Ending(
        lambda: after_the_first_audio(
            a_delta("Gu"), a_delta("ten"), a_completed("Guten Tag.")
        ),
        [_interim("Gu"), _interim("Guten"), _final("Guten Tag.")],
        HopClose.NORMAL,
    ),
    "session-and-turn-events-skipped": Ending(
        lambda: after_the_first_audio(
            wire.SessionCreated(
                session=wire.TranscriptionSession(type="transcription")
            ),
            wire.SessionUpdated(
                session=wire.TranscriptionSession(type="transcription")
            ),
            wire.SpeechStarted(audio_start_ms=0, item_id=AN_ITEM),
            wire.SpeechStopped(audio_end_ms=1, item_id=AN_ITEM),
            wire.AudioCommitted(item_id=AN_ITEM, previous_item_id=None),
            wire.ItemCreated(
                previous_item_id=None,
                item=wire.UserMessageItem(id=AN_ITEM),
            ),
            a_completed("Ein Wort."),
        ),
        [_final("Ein Wort.")],
        HopClose.NORMAL,
    ),
    "failed-then-error": Ending(
        lambda: after_the_first_audio(a_failed_item(), an_error_event()),
        [],
        HopClose.SERVICE_FAILED,
    ),
    "error-alone": Ending(
        lambda: after_the_first_audio(an_error_event()), [], HopClose.SERVICE_FAILED
    ),
    **HEARING_ENDS,
    "busy-at-the-upgrade": Ending(busy_at_the_upgrade, [], HopClose.BUSY),
    "drained-then-reopened": Ending(
        lambda: one_after_another(draining(), sending(a_completed("Nach der Pause."))),
        [_final("Nach der Pause.")],
        HopClose.NORMAL,
    ),
    "drained-then-no-session-opens": Ending(
        lambda: one_after_another(draining(), busy_at_the_upgrade()),
        [],
        HopClose.SERVICE_FAILED,
    ),
    **{
        f"refused-{status}": Ending(
            lambda status=status: denying(status), [], HopClose.SERVICE_FAILED
        )
        for status in (401, 403, 503)
    },
}
