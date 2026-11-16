"""An exception in a build step leaves its own error line and a warning build."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from presentator.adapters.builds import BuildStep
from presentator.contracts.decks import BuildFailure, ToolchainUnavailable
from tests.presentator.adapters.support.build_support import (
    builds_ready_for,
    lines_of,
    the_error_line,
)
from tests.support.deck_source_support import a_deck, a_source
from tests.support.git_remote import GitRemote
from tests.support.log_lines import json_logged

_PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)


class _ToolchainThatBreaks:
    """A toolchain whose step raises, as a bug in this host's own code does."""

    def ran(self, step: BuildStep) -> BuildFailure | ToolchainUnavailable | None:
        raise RuntimeError(step.name)


def test_an_exception_in_a_step_leaves_one_error_line_and_a_warning_for_the_build(
    remote: GitRemote,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    remote.commit({"a/slides.md": "# A talk\n"}, at=_PUSHED_AT)
    source = a_source(remote.url)
    builds = builds_ready_for(tmp_path, source, toolchain=_ToolchainThatBreaks())
    deck = a_deck("a", commit=remote.head)

    with json_logged(capsys) as logged, pytest.raises(RuntimeError):
        builds.build(deck, source=source)

    step = the_error_line(logged)
    assert (step["event"], step["error_type"]) == ("build_step.ended", "RuntimeError")
    (build,) = lines_of(logged, "deck_build.ended")
    assert (build["outcome"], build["error_type"], build["level"]) == (
        "failed",
        "RuntimeError",
        "warning",
    )
