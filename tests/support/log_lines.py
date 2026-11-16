"""The JSON lines a process writes through logline."""

import json
from collections.abc import Generator
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


def json_lines_among(output: str) -> list[LogLine]:
    """The JSON lines of an output that holds other text too, such as a traceback."""
    return json_lines(
        "\n".join(one for one in output.splitlines() if one.startswith("{"))
    )


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
def json_logged(capsys: pytest.CaptureFixture[str]) -> Generator[LoggedText]:
    """JSON logging on the output `capsys` holds, until the block ends.

    The handler binds the output as it is when `configure` runs, which a fixture
    cannot do for a test: pytest swaps the captured streams after the fixtures.
    """
    with configured("test"):
        yield LoggedText(capsys)
