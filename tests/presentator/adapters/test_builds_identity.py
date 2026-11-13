"""The runner answers the toolchain identity it was handed."""

from pathlib import Path

from presentator.contracts.decks import ToolchainIdentity
from tests.presentator.adapters.support.build_support import (
    builds_under,
    on_this_machine,
)


def test_the_runner_answers_the_identity_it_was_handed(tmp_path: Path) -> None:
    handed = ToolchainIdentity("c" * 64)
    builds = builds_under(
        tmp_path, toolchain=on_this_machine(tmp_path), identity=handed
    )

    assert builds.toolchain_identity() == handed
