"""The answer start read: end of speech to first audio, joined by trace."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import ModuleType

_NOON = datetime(2026, 11, 19, 12, tzinfo=UTC)


@pytest.fixture
def read(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("answer_start")


def _speech_end(trace: str, *, at_ms: float, waited_ms: float = 500.0) -> str:
    """The speech service's wait line, whose wait began at `at_ms` after noon."""
    written = _NOON + timedelta(milliseconds=at_ms + waited_ms)
    return json.dumps(
        {
            "ts": written.isoformat(),
            "event": "turn_end_wait.ended",
            "trace_id": trace,
            "duration_ms": waited_ms,
        }
    )


def _answer(
    trace: str, *, asked_at_ms: float, first_audio_ms: float | None = 900.0
) -> str:
    """The app's answer line of an ask made `asked_at_ms` after noon."""
    lasted_ms = 3000.0
    line: dict[str, object] = {
        "ts": (_NOON + timedelta(milliseconds=asked_at_ms + lasted_ms)).isoformat(),
        "event": "answer.ended",
        "trace_id": trace,
        "duration_ms": lasted_ms,
        "outcome": "ok",
    }
    if first_audio_ms is not None:
        line["first_output_ms"] = first_audio_ms
    return json.dumps(line)


def test_each_joined_turn_counts_from_the_end_of_speech_to_the_first_audio(
    read: ModuleType,
) -> None:
    lines = [
        _speech_end("a", at_ms=0),
        _answer("a", asked_at_ms=1200, first_audio_ms=900),
        _speech_end("b", at_ms=10_000),
        _answer("b", asked_at_ms=11_000, first_audio_ms=1500),
    ]

    figure = read.answer_start(lines)

    assert sorted(figure.answer_starts_ms) == pytest.approx([2100, 2500])
    assert figure.unjoined == ()


def test_the_report_prints_the_turn_count_and_the_nearest_rank_percentiles(
    read: ModuleType,
) -> None:
    lines: list[str] = []
    for turn in range(20):
        lines += [
            _speech_end(str(turn), at_ms=turn * 10_000),
            _answer(str(turn), asked_at_ms=turn * 10_000, first_audio_ms=turn + 1),
        ]

    printed = read.report(read.answer_start(lines))

    assert printed.splitlines() == ["turns 20", "p50_ms 10", "p95_ms 19"]


@pytest.mark.parametrize(
    ("lines", "why"),
    [
        pytest.param(
            [_answer("t", asked_at_ms=0)], "no_end_of_speech", id="asked-fresh"
        ),
        pytest.param([_speech_end("t", at_ms=0)], "no_answer", id="never-asked"),
        pytest.param(
            [
                _speech_end("t", at_ms=0),
                _answer("t", asked_at_ms=1, first_audio_ms=None),
            ],
            "no_first_audio",
            id="no-audio",
        ),
        pytest.param(
            [
                _speech_end("t", at_ms=0),
                _answer("t", asked_at_ms=1),
                _answer("t", asked_at_ms=2),
            ],
            "repeated",
            id="two-answers",
        ),
        pytest.param(
            [
                _speech_end("t", at_ms=0),
                _answer("t", asked_at_ms=1).replace(
                    '"duration_ms": 3000.0', '"duration_ms": "3"'
                ),
            ],
            "malformed",
            id="text-duration",
        ),
    ],
)
def test_a_trace_that_cannot_join_is_named_with_its_reason_and_no_figure(
    read: ModuleType, lines: list[str], why: str
) -> None:
    printed = read.report(read.answer_start(lines))

    assert printed.splitlines() == ["turns 0", f"unjoined t {why}"]


def test_the_command_reads_a_file_or_stdin_skips_other_text_and_fails_without_a_turn(
    read: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    joined = tmp_path / "joined.jsonl"
    joined.write_text(
        "\n".join(
            [
                "a banner",
                json.dumps({"event": "speak.ended", "trace_id": "a"}),
                _speech_end("a", at_ms=0),
                _answer("a", asked_at_ms=0),
            ]
        )
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(_answer("b", asked_at_ms=0)))

    joined_status = read.main([str(joined)])
    joined_out = capsys.readouterr().out
    unjoined_status = read.main([])

    assert (joined_status, unjoined_status) == (0, 1)
    assert joined_out.splitlines() == [
        "turns 1",
        "p50_ms 900",
        "p95_ms 900",
        "skipped_lines 1",
    ]
    assert capsys.readouterr().out.splitlines() == [
        "turns 0",
        "unjoined b no_end_of_speech",
    ]
