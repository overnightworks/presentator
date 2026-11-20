"""The bound on a talk's size is counted in whole bytes and rounded up."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from presentator.contracts.decks import (
    Artefacts,
    BuildFailure,
    BuildFailureReason,
    ToolchainUnavailable,
)
from tests.presentator.adapters.support.build_support import (
    FINDS_ITS_OUTPUT,
    builds_ready_for,
    with_the_toolchain,
)
from tests.support.deck_source_support import a_deck, a_source
from tests.support.git_remote import EXAMPLE_SLUG, GitRemote

_PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)
_ONLY_A_MEGABYTE_MAY_BE_LEFT = 1
_A_MEGABYTE = 1024 * 1024
# A talk of exactly the bytes a test asks for, and an empty PDF.
_A_TOOLCHAIN_THAT_WRITES_BYTES = (
    FINDS_ITS_OUTPUT
    + """case "$out" in
    *.pdf) : > "$out";;
    *) mkdir -p "$out"; head -c {talk} /dev/zero > "$out/index.html";;
esac
"""
)


def _built_with_a_talk_of_the_bound_plus(
    bytes_over: int,
    remote: GitRemote,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Artefacts | BuildFailure | ToolchainUnavailable:
    remote.commit_example_deck(at=_PUSHED_AT)
    source = a_source(remote.url)
    with_the_toolchain(
        monkeypatch,
        tmp_path,
        _A_TOOLCHAIN_THAT_WRITES_BYTES.format(
            talk=_ONLY_A_MEGABYTE_MAY_BE_LEFT * _A_MEGABYTE + bytes_over
        ),
    )
    builds = builds_ready_for(
        tmp_path, source, output_megabytes=_ONLY_A_MEGABYTE_MAY_BE_LEFT
    )
    return builds.build(a_deck(EXAMPLE_SLUG, commit=remote.head), source=source)


@pytest.mark.parametrize(
    ("bytes_over", "outcome"),
    [
        (0, "kept"),
        (1, "refused with the rounded-up total"),
    ],
)
def test_a_talk_is_kept_up_to_the_megabytes_and_refused_one_byte_over(
    remote: GitRemote,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bytes_over: int,
    outcome: str,
) -> None:
    built = _built_with_a_talk_of_the_bound_plus(
        bytes_over, remote, monkeypatch, tmp_path
    )

    if outcome == "kept":
        assert isinstance(built, Artefacts)
    else:
        assert built == BuildFailure(
            reason=BuildFailureReason.OUTPUT_MEGABYTES,
            written_megabytes=_ONLY_A_MEGABYTE_MAY_BE_LEFT + 1,
            allowed_megabytes=_ONLY_A_MEGABYTE_MAY_BE_LEFT,
        )
