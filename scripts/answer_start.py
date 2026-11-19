"""How soon a spoken answer begins: end of speech to first audio, per turn.

`docs/co-presenter.md` (How soon an answer begins) owns the figure, the lines it
joins by `trace_id`, what the command prints and its gaps.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

_END_OF_SPEECH_EVENT: Final = "turn_end_wait.ended"
_ANSWER_EVENT: Final = "answer.ended"
_JOINED_EVENTS: Final = frozenset({_END_OF_SPEECH_EVENT, _ANSWER_EVENT})
_PERCENTILES: Final = (50, 95)
_DESCRIPTION: Final = "End of speech to first audio per turn: p50, p95, turns."


class Unjoined(StrEnum):
    """Why a trace gives no figure."""

    NO_END_OF_SPEECH = "no_end_of_speech"
    NO_ANSWER = "no_answer"
    NO_FIRST_AUDIO = "no_first_audio"
    REPEATED = "repeated"
    MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class Figure:
    """Each joined turn's end of speech to first audio, and every trace left out."""

    answer_starts_ms: tuple[float, ...]
    unjoined: tuple[tuple[str, Unjoined], ...]
    skipped_lines: int


def _milliseconds(line: Mapping[str, object], key: str) -> float:
    value = line[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(key)
    return value


def _start_ms(line: Mapping[str, object]) -> float:
    """When the line's step started, in epoch milliseconds: `ts` less its duration."""
    written = datetime.fromisoformat(str(line["ts"])).timestamp() * 1000
    return written - _milliseconds(line, "duration_ms")


def _answer_start_ms(lines: Sequence[Mapping[str, object]]) -> float | Unjoined:
    by_event: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for line in lines:
        by_event[str(line["event"])].append(line)
    speech, answers = by_event[_END_OF_SPEECH_EVENT], by_event[_ANSWER_EVENT]
    if len(speech) > 1 or len(answers) > 1:
        return Unjoined.REPEATED
    if not speech:
        return Unjoined.NO_END_OF_SPEECH
    if not answers:
        return Unjoined.NO_ANSWER
    if answers[0].get("first_output_ms") is None:
        return Unjoined.NO_FIRST_AUDIO
    try:
        (answer,) = answers
        first_audio = _start_ms(answer) + _milliseconds(answer, "first_output_ms")
        return first_audio - _start_ms(speech[0])
    except KeyError, TypeError, ValueError:
        return Unjoined.MALFORMED


def _json_object(text: str) -> dict[str, object] | None:
    """The line as a JSON object, or None for any other text, such as a banner."""
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        return None
    return cast("dict[str, object]", parsed) if isinstance(parsed, dict) else None


def answer_start(text_lines: Iterable[str]) -> Figure:
    """Join the joined events of `text_lines` by trace into one figure."""
    by_trace: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    skipped = 0
    for text in text_lines:
        line = _json_object(text)
        if line is None:
            skipped += 1
        elif line.get("event") in _JOINED_EVENTS:
            by_trace[str(line.get("trace_id"))].append(line)
    starts: list[float] = []
    unjoined: list[tuple[str, Unjoined]] = []
    for trace_id, lines in by_trace.items():
        joined = _answer_start_ms(lines)
        if isinstance(joined, Unjoined):
            unjoined.append((trace_id, joined))
        else:
            starts.append(joined)
    return Figure(tuple(starts), tuple(unjoined), skipped)


def _nearest_rank(ordered: Sequence[float], percentile: int) -> float:
    return ordered[math.ceil(percentile / 100 * len(ordered)) - 1]


def report(figure: Figure) -> str:
    """The figure as the lines the script prints."""
    ordered = sorted(figure.answer_starts_ms)
    lines = [f"turns {len(ordered)}"]
    if ordered:
        lines += [f"p{p}_ms {round(_nearest_rank(ordered, p))}" for p in _PERCENTILES]
    lines += [f"unjoined {trace} {why}" for trace, why in figure.unjoined]
    if figure.skipped_lines:
        lines.append(f"skipped_lines {figure.skipped_lines}")
    return "\n".join(lines) + "\n"


def main(arguments: Sequence[str]) -> int:
    """Print the figure of the lines read; 1 when no turn joins."""
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument("logs", nargs="*", type=Path, help="JSON log files; else stdin")
    paths: list[Path] = parser.parse_args(arguments).logs
    if paths:
        texts = [line for path in paths for line in path.read_text().splitlines()]
    else:
        texts = sys.stdin.read().splitlines()
    figure = answer_start(texts)
    sys.stdout.write(report(figure))
    return 0 if figure.answer_starts_ms else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
