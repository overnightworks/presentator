"""A source whose repository cannot be read ends its run; the others go on."""

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gitmirror.model import CredentialReference
from presentator.application.decks import Decks
from presentator.contracts.decks import Source, SourceRunFailure, SourceRunOutcome
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
    spoiled_remote: GitRemote
    healthy_remote: GitRemote
    credentials: _Credentials
    tmp_path: Path

    def mirror_directory_of_the_spoiled(self) -> Path:
        mirror = mirrors_under(self.tmp_path).of(self.spoiled)
        assert mirror is not None
        return mirror.directory

    def run_of(self, source: Source) -> tuple[SourceRunOutcome, str | None, str | None]:
        newest = self.fakes.source_runs.newest(source.id)
        assert newest is not None
        return newest.outcome, newest.commit, newest.reason

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
    decks = decks_over(
        sources=FakeSourceStore(sources=[spoiled, healthy]),
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


def _a_credential_git_cannot_be_sent(instance: _Instance) -> None:
    instance.credentials.broken.add(instance.spoiled.id)


@pytest.mark.parametrize(
    ("spoil", "reason"),
    [
        (_a_file_where_the_mirror_stood, SourceRunFailure.FAILED),
        (_a_commit_date_no_calendar_can_hold, SourceRunFailure.FAILED),
        (_a_credential_git_cannot_be_sent, SourceRunFailure.CREDENTIAL_UNRESOLVABLE),
    ],
    ids=["mirror-cannot-be-opened", "commit-date-unreadable", "credential-unsendable"],
)
def test_a_source_that_cannot_be_read_ends_its_run_failed_and_the_next_is_taken_in(
    spoil: Callable[[_Instance], None],
    reason: SourceRunFailure,
    instance: _Instance,
) -> None:
    spoil(instance)

    instance.refresh_after_the_healthy_source_pushed()

    assert instance.run_of(instance.spoiled) == (
        SourceRunOutcome.FAILURE,
        None,
        reason,
    )
    assert instance.fakes.store.get("talk") is not None
    assert "talk" not in instance.fakes.store.removed
    assert instance.fakes.store.get("later") is not None
    assert "later" in instance.fakes.builder.built
    assert BuildRun("nobody-names-me") in instance.fakes.builder.removed


def test_a_folder_whose_manifest_became_a_submodule_stays_present_and_unmarked(
    instance: _Instance,
) -> None:
    instance.spoiled_remote.commit_gitlink({}, gitlink="talk/deck.toml", at=_NEXT_PUSH)

    instance.refresh_after_the_healthy_source_pushed()

    outcome, commit, reason = instance.run_of(instance.spoiled)
    assert (outcome, reason) == (SourceRunOutcome.SUCCESS, None)
    assert commit == instance.spoiled_remote.head
    assert instance.fakes.store.get("talk") is not None
    assert "talk" not in instance.fakes.store.removed
    assert instance.fakes.store.get("later") is not None


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
        {f"{folder}/slides.md": "# Fresh\n"},
        gitlink=f"{folder}/deck.toml",
        at=_NEXT_PUSH,
    )

    instance.refresh_after_the_healthy_source_pushed()

    assert instance.run_of(instance.spoiled)[0] is SourceRunOutcome.SUCCESS
    assert {deck.slug for deck in instance.fakes.store.all()} == {
        "talk",
        "first",
        "later",
    }
