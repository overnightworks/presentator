"""Each fetch of a source writes one line with its outcome and duration, no address.

A failure's line and the state its source is listed in agree on whether it is
a refusal.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from gitmirror.model import CredentialReference, CredentialResolver
from presentator.adapters.secrets import generate_deploy_key
from presentator.application.source_state import source_state_of
from presentator.contracts.decks import (
    SourceRun,
    SourceRunFailure,
    SourceRunOutcome,
    SourceState,
)
from tests.support.deck_source_support import PUSHED_AT, a_source, folders_under
from tests.support.git_remote import GitRemote, with_the_program
from tests.support.log_lines import json_logged
from tests.support.mirror_support import FixedSecret


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


def test_a_credential_git_cannot_send_ends_the_fetch_failed_with_its_code(
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
    assert fetch["outcome"] == "failed"
    assert fetch["reason"] == SourceRunFailure.CREDENTIAL_UNRESOLVABLE.value
    assert fetch["level"] == "warning"


@dataclass(frozen=True, slots=True, kw_only=True)
class _Unread:
    """One way a source fails to be read, and whether its fetch is a refusal."""

    failure: SourceRunFailure
    refused: bool
    url: str = "https://host.example.invalid/repo.git"
    credentials: CredentialResolver | None = None
    ssh_says: tuple[str, ...] = ()


class _NoSecret:
    def resolve(self, reference: CredentialReference) -> str | None:
        return None


def _over_ssh(failure: SourceRunFailure, *says: str, refused: bool) -> _Unread:
    return _Unread(
        failure=failure,
        refused=refused,
        url="git@ssh-stub-host:talks.git",
        credentials=FixedSecret(generate_deploy_key().private_key),
        ssh_says=says,
    )


def _ssh_saying(lines: tuple[str, ...]) -> str:
    echoes = "".join(f"echo '{line}' >&2\n" for line in lines)
    return f"#!/bin/sh\n{echoes}exit 255\n"


_EVERY_UNREAD: Final = (
    _Unread(
        failure=SourceRunFailure.CREDENTIAL_UNRESOLVABLE,
        refused=False,
        credentials=_NoSecret(),
    ),
    _over_ssh(
        SourceRunFailure.REFUSED,
        "git@ssh-stub-host: Permission denied (publickey).",
        refused=True,
    ),
    _over_ssh(SourceRunFailure.FAILED, "fatal: protocol error", refused=False),
    _Unread(failure=SourceRunFailure.UNREACHABLE, refused=False),
    _over_ssh(
        SourceRunFailure.HOST_KEY_REFUSED,
        "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!",
        "Host key verification failed.",
        refused=True,
    ),
)


def test_every_failure_kind_has_a_way_to_be_unread() -> None:
    assert {unread.failure for unread in _EVERY_UNREAD} == set(SourceRunFailure)


@pytest.mark.parametrize(
    "unread", _EVERY_UNREAD, ids=[unread.failure.value for unread in _EVERY_UNREAD]
)
def test_a_fetch_line_and_the_listed_state_agree_on_whether_a_failure_is_a_refusal(
    unread: _Unread,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if unread.ssh_says:
        with_the_program(monkeypatch, tmp_path, "ssh", _ssh_saying(unread.ssh_says))
    source = a_source(unread.url)

    with json_logged(capsys) as logged:
        poll = folders_under(tmp_path, credentials=unread.credentials).folders(source)

    assert poll.failure is unread.failure
    [fetch] = logged.lines
    assert fetch["event"] == "source_fetch.ended"
    assert fetch["source_id"] == source.id
    assert fetch["reason"] == unread.failure.value
    outcome, level = ("refused", "info") if unread.refused else ("failed", "warning")
    assert fetch["outcome"] == outcome
    assert fetch["level"] == level
    assert source.url not in logged.text
    run = SourceRun(
        source_id=source.id,
        at=PUSHED_AT,
        outcome=SourceRunOutcome.FAILURE,
        commit=None,
        reason=poll.failure,
    )
    assert (source_state_of(run) is SourceState.REFUSED) is unread.refused
