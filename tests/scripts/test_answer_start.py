"""The answer start read: end of speech to first audio, joined by trace."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from tests.scripts.support.answer_start_lines import answer, speech_end

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import ModuleType


@pytest.fixture
def read(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("answer_start")


def test_each_joined_turn_counts_from_the_end_of_speech_to_the_first_audio(
    read: ModuleType,
) -> None:
    lines = [
        speech_end("a", at_ms=0),
        answer("a", asked_at_ms=1200, first_audio_ms=900),
        speech_end("b", at_ms=10_000),
        answer("b", asked_at_ms=11_000, first_audio_ms=1500),
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
            speech_end(str(turn), at_ms=turn * 10_000),
            answer(str(turn), asked_at_ms=turn * 10_000, first_audio_ms=turn + 1),
        ]

    printed = read.report(read.answer_start(lines))

    assert printed.splitlines() == ["turns 20", "p50_ms 10", "p95_ms 19"]


@pytest.mark.parametrize(
    ("lines", "why"),
    [
        pytest.param(
            [answer("t", asked_at_ms=0)], "no_end_of_speech", id="asked-fresh"
        ),
        pytest.param([speech_end("t", at_ms=0)], "no_answer", id="never-asked"),
        pytest.param(
            [
                speech_end("t", at_ms=0),
                answer("t", asked_at_ms=1, first_audio_ms=None),
            ],
            "no_first_audio",
            id="no-audio",
        ),
        pytest.param(
            [
                speech_end("t", at_ms=0),
                answer("t", asked_at_ms=1, first_audio_ms=None, reason="silent"),
            ],
            "silent",
            id="kept-silent",
        ),
        pytest.param(
            [
                speech_end("t", at_ms=0),
                answer(
                    "t", asked_at_ms=1, first_audio_ms=None, reason="unspoken"
                ).replace('"ok"', '"failed"'),
            ],
            "no_first_audio",
            id="unspoken",
        ),
        pytest.param(
            [
                speech_end("t", at_ms=0),
                answer("t", asked_at_ms=1),
                answer("t", asked_at_ms=2),
            ],
            "repeated",
            id="two-answers",
        ),
        pytest.param(
            [
                speech_end("t", at_ms=0),
                answer("t", asked_at_ms=1).replace(
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
) -> None:
    joined = tmp_path / "joined.jsonl"
    joined.write_text(
        "\n".join(
            [
                "a banner",
                json.dumps({"event": "speak.ended", "trace_id": "a"}),
                speech_end("a", at_ms=0),
                answer("a", asked_at_ms=0),
            ]
        )
    )

    joined_status = read.main([str(joined)])
    unjoined = subprocess.run(  # noqa: S603 - the interpreter and this repository's own script
        [sys.executable, str(read.__file__)],
        input=answer("b", asked_at_ms=0),
        capture_output=True,
        text=True,
        check=False,
    )

    assert (joined_status, unjoined.returncode) == (0, 1)
    assert capsys.readouterr().out.splitlines() == [
        "turns 1",
        "p50_ms 900",
        "p95_ms 900",
        "skipped_lines 1",
    ]
    assert unjoined.stdout.splitlines() == [
        "turns 0",
        "unjoined b no_end_of_speech",
    ]
