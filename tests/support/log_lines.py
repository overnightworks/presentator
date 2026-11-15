"""The JSON lines a process writes through logline."""

import json
from typing import cast

import pytest
from logline import REQUEST_EVENT
from pydantic import JsonValue

type LogLine = dict[str, JsonValue]


def json_lines(output: str) -> list[LogLine]:
    """Every line of `output` as logline wrote it; a line that is no object fails."""
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def request_lines(caplog: pytest.LogCaptureFixture) -> list[LogLine]:
    """The request lines logline has written so far, as the records carry them."""
    return [
        cast("LogLine", record.msg)
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == REQUEST_EVENT
    ]


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
