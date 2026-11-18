"""Each fetch of a source writes one line with its outcome and duration, no address."""

from pathlib import Path

import pytest

from gitmirror.model import CredentialReference
from presentator.adapters.secrets import generate_deploy_key
from presentator.contracts.decks import SourceRunFailure
from tests.support.deck_source_support import PUSHED_AT, a_source, folders_under
from tests.support.git_remote import GitRemote, with_the_program
from tests.support.log_lines import json_logged
from tests.support.mirror_support import FixedSecret

# What ssh writes when a host answers with another key than the one it trusts.
_SSH_REFUSING_THE_HOST_KEY = """#!/bin/sh
echo "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@" >&2
echo "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @" >&2
echo "Host key verification failed." >&2
exit 255
"""


def test_a_poll_that_reaches_a_source_writes_one_ok_fetch_line_with_its_duration(
    remote: GitRemote,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    remote.commit_example_deck(at=PUSHED_AT)
    source = a_source(remote.url)

    with json_logged(capsys) as logged:
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

    with json_logged(capsys) as logged:
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

    with json_logged(capsys) as logged:
        poll = folders_under(tmp_path).folders(unreachable)

    assert poll.folders is None
    assert poll.failure is SourceRunFailure.UNREACHABLE
    [fetch] = logged.lines
    assert fetch["event"] == "source_fetch.ended"
    assert fetch["source_id"] == unreachable.id
    assert fetch["outcome"] == "failed"
    assert fetch["reason"] == SourceRunFailure.UNREACHABLE.value
    assert "error_type" not in fetch
    assert fetch["level"] == "warning"
    assert isinstance(fetch["duration_ms"], float)
    assert unreachable.url not in logged.text


def test_a_credential_git_cannot_send_ends_the_fetch_refused_with_its_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Unsendable:
        def resolve(self, reference: CredentialReference) -> str | None:
            return "not\nsendable"

    source = a_source("https://host.example.invalid/repo.git")

    with json_logged(capsys) as logged:
        poll = folders_under(tmp_path, credentials=_Unsendable()).folders(source)

    assert poll.failure is SourceRunFailure.CREDENTIAL_UNRESOLVABLE
    [fetch] = logged.lines
    assert fetch["outcome"] == "refused"
    assert fetch["reason"] == SourceRunFailure.CREDENTIAL_UNRESOLVABLE.value
    assert fetch["level"] == "info"


def test_a_host_that_answers_with_another_key_ends_the_fetch_failed_with_its_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with_the_program(monkeypatch, tmp_path, "ssh", _SSH_REFUSING_THE_HOST_KEY)
    secret = FixedSecret(generate_deploy_key().private_key)
    source = a_source("git@ssh-stub-host:talks.git")

    with json_logged(capsys) as logged:
        poll = folders_under(tmp_path, credentials=secret).folders(source)

    assert poll.failure is SourceRunFailure.HOST_KEY_REFUSED
    [fetch] = logged.lines
    assert fetch["outcome"] == "failed"
    assert fetch["reason"] == SourceRunFailure.HOST_KEY_REFUSED.value
    assert fetch["level"] == "warning"
