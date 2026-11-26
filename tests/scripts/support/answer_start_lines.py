"""The log lines `scripts/answer_start.py` reads, built for tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

NOON = datetime(2026, 11, 19, 12, tzinfo=UTC)


def speech_end(trace: str, *, at_ms: float, waited_ms: float = 500.0) -> str:
    """The speech service's wait line, whose wait began at `at_ms` after noon."""
    written = NOON + timedelta(milliseconds=at_ms + waited_ms)
    return json.dumps(
        {
            "ts": written.isoformat(),
            "event": "turn_end_wait.ended",
            "trace_id": trace,
            "duration_ms": waited_ms,
        }
    )


def answer(
    trace: str,
    *,
    asked_at_ms: float,
    first_audio_ms: float | None = 900.0,
    reason: str | None = None,
) -> str:
    """The app's answer line of an ask made `asked_at_ms` after noon."""
    lasted_ms = 3000.0
    line: dict[str, object] = {
        "ts": (NOON + timedelta(milliseconds=asked_at_ms + lasted_ms)).isoformat(),
        "event": "answer.ended",
        "trace_id": trace,
        "duration_ms": lasted_ms,
        "outcome": "ok",
    }
    if first_audio_ms is not None:
        line["first_output_ms"] = first_audio_ms
    if reason is not None:
        line["reason"] = reason
    return json.dumps(line)
