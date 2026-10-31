"""The lobby's built shell: read once, hashed for the policy, filled per reader."""

from pathlib import Path

import pytest
from webauth.policies import default_content_security_policy

from presentator.api.lobby_shell import (
    LANGUAGE_MARK,
    THEME_MARK,
    LobbyBuildError,
    LobbyShell,
)
from presentator.contracts.preferences import Appearance, ThemeChoice
from tests.support.lobby import ENGLISH

_HTML = (
    f'<html lang="{LANGUAGE_MARK}" data-theme="{THEME_MARK}"><body>{{}}</body></html>'
)
_BOOTSTRAP = "<script>import('/_app/start.js');</script>"
_REBUILT_BOOTSTRAP = "<script>import('/_app/rebuilt.js');</script>"


def _a_build(directory: Path, page: str) -> Path:
    (directory / "_app").mkdir(parents=True)
    (directory / "index.html").write_text(page, encoding="utf-8")
    return directory


def test_the_shell_is_filled_with_the_readers_language_and_theme(
    tmp_path: Path,
) -> None:
    shell = LobbyShell.read(_a_build(tmp_path, _HTML.format(_BOOTSTRAP)))

    page = shell.filled(Appearance(text=ENGLISH, theme=ThemeChoice.DARK))

    assert f'lang="{ENGLISH.language_tag}"' in page
    assert 'data-theme="dark"' in page


def test_the_bootstrap_hash_is_a_source_the_policy_takes(tmp_path: Path) -> None:
    shell = LobbyShell.read(_a_build(tmp_path, _HTML.format(_BOOTSTRAP)))

    policy = default_content_security_policy([shell.bootstrap_hash])

    assert f"'{shell.bootstrap_hash}'" in policy


def test_the_bootstrap_hash_follows_the_built_script(tmp_path: Path) -> None:
    one = LobbyShell.read(_a_build(tmp_path / "one", _HTML.format(_BOOTSTRAP)))
    again = LobbyShell.read(_a_build(tmp_path / "again", _HTML.format(_BOOTSTRAP)))
    rebuilt = LobbyShell.read(
        _a_build(tmp_path / "rebuilt", _HTML.format(_REBUILT_BOOTSTRAP))
    )

    assert one.bootstrap_hash == again.bootstrap_hash
    assert one.bootstrap_hash != rebuilt.bootstrap_hash


@pytest.mark.parametrize(
    "page",
    [
        pytest.param(_HTML.format(""), id="no-inline-script"),
        pytest.param(_HTML.format(_BOOTSTRAP * 2), id="two-inline-scripts"),
        pytest.param(f"<html><body>{_BOOTSTRAP}</body></html>", id="no-marks"),
    ],
)
def test_a_shell_this_host_cannot_serve_is_refused(tmp_path: Path, page: str) -> None:
    with pytest.raises(LobbyBuildError):
        LobbyShell.read(_a_build(tmp_path, page))


def test_a_missing_build_is_refused(tmp_path: Path) -> None:
    with pytest.raises(LobbyBuildError, match="pnpm build"):
        LobbyShell.read(tmp_path)
