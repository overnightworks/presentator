"""A toolchain that was never asked for the talk leaves a build unattempted."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from presentator.adapters.builds import BuildStep
from presentator.contracts.decks import (
    BuildFailure,
    StepKind,
    ToolchainUnavailable,
)
from tests.presentator.adapters.support.build_support import builds_ready_for
from tests.support.deck_source_support import a_deck, a_source
from tests.support.git_remote import GitRemote

_PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class _ToolchainGoneAt:
    """A toolchain that runs every step but one, which it cannot be asked for."""

    kind: StepKind

    def ran(self, step: BuildStep) -> ToolchainUnavailable | None:
        return ToolchainUnavailable() if step.name is self.kind else None


@pytest.mark.parametrize(
    ("unavailable_at", "built"),
    [
        (StepKind.BUILD, ToolchainUnavailable()),
        (StepKind.EXPORT, BuildFailure.without_words()),
    ],
    ids=["the talk step", "the PDF step after a talk step that ran"],
)
def test_only_a_talk_step_the_toolchain_never_started_leaves_the_build_unattempted(
    remote: GitRemote,
    tmp_path: Path,
    unavailable_at: StepKind,
    built: ToolchainUnavailable | BuildFailure,
) -> None:
    remote.commit({"a/slides.md": "# A talk\n"}, at=_PUSHED_AT)
    source = a_source(remote.url)
    builds = builds_ready_for(
        tmp_path, source, toolchain=_ToolchainGoneAt(kind=unavailable_at)
    )

    assert builds.build(a_deck("a", commit=remote.head), source=source) == built
    assert list(builds.builds.iterdir()) == []
