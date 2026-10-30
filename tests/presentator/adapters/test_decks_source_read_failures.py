"""A source whose repository cannot be read ends its run; the others go on."""

import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gitmirror.model import CredentialReference
from presentator.application.decks import Decks
from presentator.contracts.decks import (
    Source,
    SourceRunFailure,
    SourceRunOutcome,
    SourceState,
)
from presentator.ports.decks import BuildRun
from tests.presentator.application.support.decks_support import DecksFakes, decks_over
from tests.presentator.application.support.fakes import FakeSourceStore
from tests.support.deck_source_support import a_source, folders_under, mirrors_under
from tests.support.git_remote import GitRemote

_FIRST_PUSH = datetime(2026, 1, 15, 9, tzinfo=UTC)
_NEXT_PUSH = _FIRST_PUSH + timedelta(hours=1)
_A_TALK = {"talk/deck.toml": 'title = "Talk"\n', "talk/slides.md": "# Talk\n"}
_ANOTHER_SOURCES_NEW_TALK = {
    "later/deck.toml": 'title = "Later"\n',
    "later/slides.md": "# Later\n",
}
_A_SIBLING_FOLDER = {
    "sibling/deck.toml": 'title = "Sibling"\n',
    "sibling/slides.md": "# Sibling\n",
}
_AN_ADDRESS_NOBODY_ANSWERS = "https://decks.invalid/talk.git"
_NOT_TEXT = "\udcffolder"


@dataclass(slots=True)
class _Credentials:
    """Answers every source's reference, and what is stored for the broken ones."""

    broken: set[str] = field(default_factory=set[str])

    def resolve(self, reference: CredentialReference) -> str | None:
        return "not\nsendable" if reference.name in self.broken else "fine"


@dataclass(frozen=True, slots=True, kw_only=True)
class _Instance:
    """Two sources of one instance, the first of which a test spoils."""

    decks: Decks
    fakes: DecksFakes
    spoiled: Source
    healthy: Source
    sources: FakeSourceStore
    spoiled_remote: GitRemote
    healthy_remote: GitRemote
    credentials: _Credentials
    tmp_path: Path

    def spoiled_now(self) -> Source:
        return next(
            source for source in self.sources.sources if source.id == self.spoiled.id
        )

    def mirror_directory_of_the_spoiled(self) -> Path:
        mirror = mirrors_under(self.tmp_path).of(self.spoiled_now())
        assert mirror is not None
        return mirror.directory

    def run_of(self, source: Source) -> tuple[SourceRunOutcome, str | None, str | None]:
        newest = self.fakes.source_runs.newest(source.id)
        assert newest is not None
        return newest.outcome, newest.commit, newest.reason

    def board_state_of_the_spoiled(self) -> SourceState:
        return next(
            listed.state
            for listed in self.decks.listed_sources()
            if listed.name == self.spoiled.name
        )

    def refresh_after_the_healthy_source_pushed(self) -> None:
        self.healthy_remote.commit(_ANOTHER_SOURCES_NEW_TALK, at=_NEXT_PUSH)
        self.fakes.builder.on_disk.add(BuildRun("nobody-names-me"))
        self.decks.refresh()


@pytest.fixture
def instance(
    remote: GitRemote,
    another_remote: GitRemote,
    tmp_path: Path,
) -> _Instance:
    spoiled = replace(a_source(remote.url), id="spoiled", name="spoiled")
    healthy = replace(a_source(another_remote.url), id="healthy", name="healthy")
    fakes = DecksFakes()
    credentials = _Credentials()
    sources = FakeSourceStore(sources=[spoiled, healthy])
    decks = decks_over(
        sources=sources,
        mirror=folders_under(tmp_path, credentials=credentials),
        fakes=fakes,
    )
    remote.commit(_A_TALK, at=_FIRST_PUSH)
    another_remote.commit(
        {"first/deck.toml": 'title = "First"\n', "first/slides.md": "# First\n"},
        at=_FIRST_PUSH,
    )
    decks.refresh()
    assert fakes.store.get("talk") is not None
    return _Instance(
        decks=decks,
        fakes=fakes,
        spoiled=spoiled,
        healthy=healthy,
        sources=sources,
        spoiled_remote=remote,
        healthy_remote=another_remote,
        credentials=credentials,
        tmp_path=tmp_path,
    )


def _a_file_where_the_mirror_stood(instance: _Instance) -> None:
    directory = instance.mirror_directory_of_the_spoiled()
    shutil.rmtree(directory)
    directory.write_text("not a repository", encoding="utf-8")


def _a_commit_date_no_calendar_can_hold(instance: _Instance) -> None:
    instance.spoiled_remote.commit_stamped(
        {"talk/slides.md": "# Changed\n"},
        stamp="1768467600 +9999",
    )


def _a_https_credential_git_cannot_be_sent(instance: _Instance) -> None:
    instance.sources.sources[0] = replace(
        instance.spoiled,
        url=_AN_ADDRESS_NOBODY_ANSWERS,
    )
    instance.credentials.broken.add(instance.spoiled.id)


@pytest.mark.parametrize(
    ("spoil", "reason", "board_state"),
    [
        (_a_file_where_the_mirror_stood, SourceRunFailure.FAILED, SourceState.FAILED),
        (
            _a_commit_date_no_calendar_can_hold,
            SourceRunFailure.FAILED,
            SourceState.FAILED,
        ),
        (
            _a_https_credential_git_cannot_be_sent,
            SourceRunFailure.CREDENTIAL_UNRESOLVABLE,
            SourceState.ERROR,
        ),
    ],
    ids=["mirror-cannot-be-opened", "commit-date-unreadable", "credential-unsendable"],
)
def test_a_source_that_cannot_be_read_ends_its_run_failed_and_the_next_is_taken_in(
    spoil: Callable[[_Instance], None],
    reason: SourceRunFailure,
    board_state: SourceState,
    instance: _Instance,
) -> None:
    spoil(instance)

    instance.refresh_after_the_healthy_source_pushed()

    assert instance.run_of(instance.spoiled) == (
        SourceRunOutcome.FAILURE,
        None,
        reason,
    )
    assert instance.board_state_of_the_spoiled() is board_state
    assert instance.fakes.store.get("talk") is not None
    assert "talk" not in instance.fakes.store.removed
    assert instance.fakes.store.get("later") is not None
    assert "later" in instance.fakes.builder.built
    assert BuildRun("nobody-names-me") in instance.fakes.builder.removed


def _record_every_git_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put a `git` first on PATH that logs each launch, then runs the real one."""
    real_git = shutil.which("git")
    assert real_git is not None
    log = tmp_path / "git-launches.log"
    wrapper = tmp_path / "recording-bin" / "git"
    wrapper.parent.mkdir()
    wrapper.write_text(
        f'#!/bin/sh\nprintf \'%s\\n\' "$PWD $*" >> "{log}"\nexec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{wrapper.parent}:{os.environ['PATH']}")
    return log


def test_a_https_credential_git_cannot_be_sent_starts_no_git_for_its_source(
    instance: _Instance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _a_https_credential_git_cannot_be_sent(instance)
    spoiled_mirror = str(instance.mirror_directory_of_the_spoiled())
    launches = _record_every_git_launch(instance.tmp_path, monkeypatch)

    instance.refresh_after_the_healthy_source_pushed()

    launched = launches.read_text(encoding="utf-8").splitlines()
    assert launched
    assert not [
        launch
        for launch in launched
        if spoiled_mirror in launch or _AN_ADDRESS_NOBODY_ANSWERS in launch
    ]


def assert_the_siblings_and_the_other_source_were_taken_in(
    instance: _Instance,
) -> None:
    assert instance.fakes.store.get("sibling") is not None
    assert "sibling" in instance.fakes.builder.built
    assert "later" in instance.fakes.builder.built
    assert BuildRun("nobody-names-me") in instance.fakes.builder.removed


def test_a_folder_whose_manifest_became_a_submodule_stays_present_and_unmarked(
    instance: _Instance,
) -> None:
    kept = instance.fakes.store.get("talk")
    instance.spoiled_remote.commit_gitlink(
        _A_SIBLING_FOLDER,
        gitlink="talk/deck.toml",
        at=_NEXT_PUSH,
    )

    instance.refresh_after_the_healthy_source_pushed()

    assert instance.run_of(instance.spoiled) == (
        SourceRunOutcome.SUCCESS,
        instance.spoiled_remote.head,
        None,
    )
    assert kept is not None
    assert instance.fakes.store.get("talk") == kept
    assert "talk" not in instance.fakes.store.removed
    assert_the_siblings_and_the_other_source_were_taken_in(instance)


@pytest.mark.parametrize(
    "folder",
    ["fresh", _NOT_TEXT],
    ids=["plain-name", "name-that-is-not-text"],
)
def test_a_new_folder_whose_manifest_is_a_submodule_takes_no_deck_and_stops_nothing(
    folder: str,
    instance: _Instance,
) -> None:
    instance.spoiled_remote.commit_gitlink(
        {f"{folder}/slides.md": "# Fresh\n", **_A_SIBLING_FOLDER},
        gitlink=f"{folder}/deck.toml",
        at=_NEXT_PUSH,
    )

    instance.refresh_after_the_healthy_source_pushed()

    assert instance.run_of(instance.spoiled) == (
        SourceRunOutcome.SUCCESS,
        instance.spoiled_remote.head,
        None,
    )
    assert {deck.slug for deck in instance.fakes.store.all()} == {
        "talk",
        "sibling",
        "first",
        "later",
    }
    assert_the_siblings_and_the_other_source_were_taken_in(instance)


def test_a_stored_address_with_userinfo_that_resolves_to_no_mirror_logs_the_name_only(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    address = "https://a-user:its-password@git.example.invalid/talks.git"
    name = "the-talks-source"
    source = replace(a_source(address), name=name)

    with caplog.at_level(logging.DEBUG):
        poll = folders_under(tmp_path).folders(source)

    assert poll.folders is None
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert any(name in message for message in warnings)
    assert "its-password" not in caplog.text
    assert "a-user" not in caplog.text
    assert "git.example.invalid" not in caplog.text
