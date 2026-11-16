"""A fixture remote leaves the programs other fixtures put on PATH reachable."""

import shutil
from pathlib import Path

import pytest

from tests.support.git_remote import GitRemote, with_the_program


def test_a_program_put_on_path_before_the_remote_fixture_stays_reachable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    with_the_program(monkeypatch, tmp_path, "a-stand-in", "#!/bin/sh\n")

    remote: GitRemote = request.getfixturevalue("remote")

    assert shutil.which("a-stand-in") is not None
    assert remote.url
