"""The JSON lines a process writes through logline."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

import pytest
from logline import REQUEST_EVENT
from logline.testing import configured
from pydantic import JsonValue

type LogLine = dict[str, JsonValue]


def json_lines(output: str) -> list[LogLine]:
    """Every line of `output` as logline wrote it; a line that is no object fails."""
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def events_of(logged: LoggedText, event: str) -> list[LogLine]:
    """The lines written so far under that event name."""
    return [line for line in logged.lines if line["event"] == event]


def request_lines(caplog: pytest.LogCaptureFixture) -> list[LogLine]:
    """The request lines logline has written so far, as the records carry them."""
    carried = [cast("object", record.msg) for record in caplog.records]
    lines = [cast("LogLine", msg) for msg in carried if isinstance(msg, dict)]
    return [line for line in lines if line.get("event") == REQUEST_EVENT]


class LoggedText:
    """Everything a test's process has written to standard output so far."""

    def __init__(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Read what `capsys` has captured."""
        self._capsys = capsys
        self._written = ""

    @property
    def text(self) -> str:
        """The output, which grows as the test goes on."""
        self._written += self._capsys.readouterr().out
        return self._written

    @property
    def lines(self) -> list[LogLine]:
        """The output as the lines logline wrote."""
        return json_lines(self.text)


@contextmanager
def logged_as_json(capsys: pytest.CaptureFixture[str]) -> Iterator[LoggedText]:
    """Write every log line of the block as logline does, to the test's own output.

    A fixture cannot do this: pytest swaps the captured stream between a
    fixture's setup and the test body, and logline binds the stream it finds.
    """
    with configured("test"):
        yield LoggedText(capsys)
