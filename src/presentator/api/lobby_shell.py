"""The lobby's built SvelteKit shell, read once when the host starts (ADR 0021).

The build writes one HTML shell whose one inline script boots the app. The
host hashes that script for the security policy, because the hash changes with
every build, and fills the reader's language and theme into `<html>` per
request, so the first paint has the right look without any browser storage.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Final

from presentator.contracts.preferences import Appearance

LANGUAGE_MARK: Final = "%lobby.lang%"
THEME_MARK: Final = "%lobby.theme%"
_SHELL_FILE: Final = "index.html"
_ASSETS_DIRECTORY: Final = "_app"


class LobbyBuildError(Exception):
    """The lobby's build is missing or is not the shell this host can serve."""


class _InlineScripts(HTMLParser):
    """Collects the text of every script element that has no `src`."""

    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []
        self._open: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and all(name != "src" for name, _ in attrs):
            self._open = []

    def handle_data(self, data: str) -> None:
        if self._open is not None:
            self._open.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._open is not None:
            self.texts.append("".join(self._open))
            self._open = None


@dataclass(frozen=True, slots=True)
class LobbyShell:
    """The shell's page, its bootstrap's CSP hash, and where its assets lie."""

    page: str
    bootstrap_hash: str
    assets: Path

    @classmethod
    def read(cls, build: Path) -> LobbyShell:
        """Read the shell the build left; refuse a build this host cannot serve."""
        shell = build / _SHELL_FILE
        assets = build / _ASSETS_DIRECTORY
        if not shell.is_file() or not assets.is_dir():
            message = f"{build} holds no built lobby: run `pnpm build` in frontend/"
            raise LobbyBuildError(message)
        page = shell.read_text(encoding="utf-8")
        missing = [mark for mark in (LANGUAGE_MARK, THEME_MARK) if mark not in page]
        if missing:
            message = f"{shell} lacks {', '.join(missing)}"
            raise LobbyBuildError(message)
        parser = _InlineScripts()
        parser.feed(page)
        parser.close()
        scripts = parser.texts
        if len(scripts) != 1:
            message = (
                f"{shell} has {len(scripts)} inline scripts, not the one bootstrap"
            )
            raise LobbyBuildError(message)
        return cls(page=page, bootstrap_hash=_sha256_source(scripts[0]), assets=assets)

    def filled(self, appearance: Appearance) -> str:
        """The shell in the reader's language and theme."""
        return self.page.replace(LANGUAGE_MARK, appearance.text.language_tag).replace(
            THEME_MARK, appearance.theme.value
        )


def _sha256_source(script: str) -> str:
    digest = hashlib.sha256(script.encode()).digest()
    return f"sha256-{base64.b64encode(digest).decode()}"
