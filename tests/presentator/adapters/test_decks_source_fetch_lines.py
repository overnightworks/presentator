"""Each fetch of a source writes one line with its outcome and duration, no address."""

from pathlib import Path

import pytest

from gitmirror.model import CredentialReference
from presentator.contracts.decks import SourceRunFailure
from tests.support.deck_source_support import PUSHED_AT, a_source, folders_under
from tests.support.git_remote import GitRemote
from tests.support.log_lines import logged_as_json


def test_a_poll_that_reaches_a_source_writes_one_ok_fetch_line_with_its_duration(
    remote: GitRemote,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    remote.commit_example_deck(at=PUSHED_AT)
    source = a_source(remote.url)

    with logged_as_json(capsys) as logged:
        folders_under(tmp_path).folders(source)

    [fetch] = logged.lines
    assert fetch["event"] == "source_fetch.ended"
    assert fetch["source_id"] == source.id
    assert fetch["outcome"] == "ok"
    assert fetch["level"] == "info"
    assert isinstance(fetch["duration_ms"], float)


def test_a_credential_nobody_can_resolve_ends_the_fetch_refused_with_its_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Unsendable:
        def resolve(self, reference: CredentialReference) -> str | None:
            return None

    source = a_source("https://host.example.invalid/repo.git")

    with logged_as_json(capsys) as logged:
        poll = folders_under(tmp_path, credentials=_Unsendable()).folders(source)

    assert poll.failure is SourceRunFailure.CREDENTIAL_UNRESOLVABLE
    [fetch] = logged.lines
    assert fetch["event"] == "source_fetch.ended"
    assert fetch["outcome"] == "refused"
    assert fetch["reason"] == SourceRunFailure.CREDENTIAL_UNRESOLVABLE.value
    assert fetch["level"] == "info"
    assert isinstance(fetch["duration_ms"], float)


def test_a_source_that_cannot_be_read_says_nothing_about_its_folders(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # A host that never answers, not a local path that merely does not exist:
    # the latter now reads its own reason, `failed`, and this test's own is
    # the one a dead host earns.
    unreachable = a_source("https://host.example.invalid/repo.git")

    with logged_as_json(capsys) as logged:
        poll = folders_under(tmp_path).folders(unreachable)

    assert poll.folders is None
    assert poll.failure is SourceRunFailure.UNREACHABLE
    [fetch] = logged.lines
    assert fetch["event"] == "source_fetch.ended"
    assert fetch["source_id"] == unreachable.id
    assert fetch["outcome"] == "failed"
    assert fetch["level"] == "warning"
    assert isinstance(fetch["duration_ms"], float)
    assert unreachable.url not in logged.text
