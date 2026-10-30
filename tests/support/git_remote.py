"""A real git remote, and the PATH that keeps git on it."""

import os
import shutil
import stat
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

EXAMPLE_DECK = Path(__file__).parents[2] / "examples" / "hello-deck"
EXAMPLE_SLUG = EXAMPLE_DECK.name
EXAMPLE_TITLE: str = tomllib.loads(
    (EXAMPLE_DECK / "deck.toml").read_text(encoding="utf-8")
)["title"]
MAIN_BRANCH = "main"
# A submodule entry names a commit of another repository; nothing here holds it.
_A_COMMIT_NOBODY_HOLDS = "a3f19c2b8d4e5f60718293a4b5c6d7e8f9012345"

_AUTHORSHIP = {
    "GIT_AUTHOR_NAME": "a test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "a test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def a_path_carrying(*directories: Path) -> str:
    """A PATH with git on it, because reading a deck is still spawning git."""
    git = shutil.which("git")
    assert git is not None
    beside_git = Path(git).parent
    return os.pathsep.join(str(directory) for directory in (*directories, beside_git))


def with_the_program(
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    program: str,
    script: str,
) -> None:
    """Put a program this test wrote on PATH, ahead of any real one."""
    somewhere_on_path = tmp_path / "programs-on-path"
    somewhere_on_path.mkdir(exist_ok=True)
    stand_in = somewhere_on_path / program
    stand_in.write_text(script, encoding="utf-8")
    stand_in.chmod(stand_in.stat().st_mode | stat.S_IEXEC)
    machine.setenv("PATH", a_path_carrying(somewhere_on_path))


def _git(inside: Path, *arguments: str, at: datetime | None = None) -> str:
    stamped = {} if at is None else {"GIT_COMMITTER_DATE": at.isoformat()}
    ran = subprocess.run(
        ["git", *arguments],
        capture_output=True,
        check=True,
        cwd=inside,
        env={**os.environ, **_AUTHORSHIP, **stamped},
    )
    return ran.stdout.decode().strip()


@dataclass(frozen=True, slots=True, kw_only=True)
class GitRemote:
    """A bare repository a mirror can pull from, and the tree that fills it."""

    bare: Path
    work: Path

    @property
    def url(self) -> str:
        """The address a source is configured with."""
        return self.bare.as_uri()

    @property
    def head(self) -> str:
        """The commit this remote's newest push wrote."""
        return _git(self.work, "rev-parse", "HEAD")

    def commit(self, files: Mapping[str, str], *, at: datetime) -> None:
        """Write the files, commit them at that moment, and push."""
        self._write(files)
        self._push(at=at)

    def commit_gitlink(
        self,
        files: Mapping[str, str],
        *,
        gitlink: str,
        at: datetime,
    ) -> None:
        """Commit a submodule entry at that path beside the files, and push."""
        self._write(files)
        _git(self.work, "add", "--all")
        _git(
            self.work,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{_A_COMMIT_NOBODY_HOLDS},{gitlink}",
        )
        _git(self.work, "commit", "--quiet", "--message", "a push", at=at)
        _git(self.work, "push", "--quiet", "origin", MAIN_BRANCH)

    def commit_stamped(self, files: Mapping[str, str], *, stamp: str) -> None:
        """Push a commit whose author and committer stamp is exactly that.

        `git commit` normalises a date it dislikes, so the commit object is
        written by hand: that is how a stamp git cannot print as a date reaches
        a mirror from a source that took the commit in unchecked.
        """
        self._write(files)
        _git(self.work, "add", "--all")
        raw = (
            f"tree {_git(self.work, 'write-tree')}\n"
            f"parent {self.head}\n"
            f"author a test <test@example.invalid> {stamp}\n"
            f"committer a test <test@example.invalid> {stamp}\n\n"
            "a push\n"
        )
        written = subprocess.run(
            ["git", "hash-object", "-t", "commit", "-w", "--stdin"],
            capture_output=True,
            check=True,
            cwd=self.work,
            input=raw.encode(),
        )
        _git(self.work, "update-ref", "HEAD", written.stdout.decode().strip())
        _git(self.work, "push", "--quiet", "origin", MAIN_BRANCH)

    def _write(self, files: Mapping[str, str]) -> None:
        for path, content in files.items():
            written = self.work / path
            written.parent.mkdir(parents=True, exist_ok=True)
            written.write_text(content, encoding="utf-8")

    def remove(self, folder: str, *, at: datetime) -> None:
        """Delete the folder from the tree, commit that deletion, and push."""
        shutil.rmtree(self.work / folder)
        self._push(at=at)

    def commit_example_deck(self, *, at: datetime, into: str = EXAMPLE_SLUG) -> None:
        """Copy the repository's own example deck in and push it."""
        shutil.copytree(EXAMPLE_DECK, self.work / into, dirs_exist_ok=True)
        self._push(at=at)

    def _push(self, *, at: datetime) -> None:
        _git(self.work, "add", "--all")
        _git(self.work, "commit", "--quiet", "--message", "a push", at=at)
        _git(self.work, "push", "--quiet", "origin", MAIN_BRANCH)


def a_remote(under: Path, named: str) -> GitRemote:
    bare = under / f"{named}.git"
    work = under / f"{named}-work"
    work.mkdir()
    _git(under, "init", "--bare", "--quiet", str(bare))
    _git(work, "init", "--quiet", "--initial-branch", MAIN_BRANCH)
    _git(work, "remote", "add", "origin", str(bare))
    return GitRemote(bare=bare, work=work)
