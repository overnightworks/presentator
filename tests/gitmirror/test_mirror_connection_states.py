"""What a pull or a check says when a source offers no credential, or a wrong one."""

from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path

import pytest

from gitmirror.mirror import check_connection, connection_state_for_failure
from gitmirror.model import ConnectionState, CredentialReference
from tests.gitmirror.test_mirror import (
    RespondingHandler,
    UnchallengedHandler,
    responding_remote,
)
from tests.support.git_remote import MAIN_BRANCH, GitRemote
from tests.support.mirror_support import A_GENEROUS_BOUND, FixedSecret, a_mirror

_A_STORED_REFERENCE = CredentialReference(name="a-stored-secret")
_DEMANDING_HOSTS = pytest.mark.parametrize(
    "handler_class",
    [RespondingHandler, UnchallengedHandler],
    ids=["basic-challenge", "bare"],
)
_NO_CREDENTIAL_WORDS = (
    "fatal: could not read Username for 'https://host.example': "
    "terminal prompts disabled"
)


@pytest.mark.parametrize(
    ("stderr", "with_credential", "without_credential"),
    [
        pytest.param(
            "fatal: Authentication failed for 'https://host.example/repo.git/'",
            ConnectionState.REFUSED,
            ConnectionState.UNREACHABLE,
            id="authentication-failed-footer",
        ),
        pytest.param(
            "remote: Invalid username or token.\n"
            "fatal: unable to access 'https://host.example/repo.git/': "
            "The requested URL returned error: 403",
            ConnectionState.REFUSED,
            ConnectionState.UNREACHABLE,
            id="invalid-username-or-token-body",
        ),
        pytest.param(
            "fatal: unable to access 'https://host.example/repo.git/': "
            "The requested URL returned error: 401",
            ConnectionState.REFUSED,
            ConnectionState.UNREACHABLE,
            id="bare-401-status",
        ),
        pytest.param(
            "git@host.example: Permission denied (publickey).\n"
            "fatal: Could not read from remote repository.",
            ConnectionState.REFUSED,
            ConnectionState.UNREACHABLE,
            id="ssh-deploy-key-no-longer-registered",
        ),
        pytest.param(
            _NO_CREDENTIAL_WORDS,
            ConnectionState.REFUSED,
            ConnectionState.UNREACHABLE,
            id="the-far-end-asks-for-a-login",
        ),
        pytest.param(
            "fatal: unable to access 'https://host.example/repo.git/': "
            "Could not resolve host: host.example",
            ConnectionState.UNREACHABLE,
            ConnectionState.UNREACHABLE,
            id="could-not-resolve-host",
        ),
        pytest.param(
            "fatal: unable to access 'https://host.example/repo.git/': "
            "SSL certificate problem: unable to get local issuer certificate",
            ConnectionState.FAILED,
            ConnectionState.FAILED,
            id="a-tls-certificate-failure-is-not-proof-the-host-never-answered",
        ),
        pytest.param(
            "fatal: unable to access 'https://host.example/repo.git/': "
            "Failed to connect to host.example port 443: Couldn't connect to server",
            ConnectionState.UNREACHABLE,
            ConnectionState.UNREACHABLE,
            id="failed-to-connect",
        ),
        pytest.param(
            "fatal: unable to access 'https://host.example/repo.git/': "
            "Connection timed out",
            ConnectionState.UNREACHABLE,
            ConnectionState.UNREACHABLE,
            id="connection-timed-out",
        ),
        pytest.param(
            "remote: Repository not found.\n"
            "fatal: repository 'https://host.example/repo.git/' not found",
            ConnectionState.FAILED,
            ConnectionState.FAILED,
            id="repository-not-found",
        ),
        pytest.param(
            "remote: Forbidden by WAF\n"
            "fatal: unable to access 'https://host.example/repo.git/': "
            "The requested URL returned error: 403",
            ConnectionState.FAILED,
            ConnectionState.FAILED,
            id="bare-403-without-auth-wording",
        ),
    ],
)
def test_a_failed_pulls_own_words_and_the_credential_offered_choose_the_reason(
    stderr: str,
    with_credential: ConnectionState,
    without_credential: ConnectionState,
) -> None:
    assert (
        connection_state_for_failure(stderr, credential_offered=True) is with_credential
    )
    assert (
        connection_state_for_failure(stderr, credential_offered=False)
        is without_credential
    )


def test_a_stored_empty_credential_pulls_a_public_repository(
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    remote.commit(
        {"a-deck/slides.md": "# Hello\n"},
        at=datetime(2026, 1, 15, 9, tzinfo=UTC),
    )
    mirror = a_mirror(
        remote.url,
        directory=tmp_path,
        credential=_A_STORED_REFERENCE,
        resolver=FixedSecret(""),
    )

    connection = mirror.connect()

    assert connection.state is ConnectionState.READY
    assert connection.revision is not None


@_DEMANDING_HOSTS
def test_a_stored_empty_credential_meets_a_demanded_login_as_unreachable(
    tmp_path: Path,
    handler_class: type[RespondingHandler],
) -> None:
    with responding_remote(
        status=HTTPStatus.UNAUTHORIZED, handler_class=handler_class
    ) as url:
        mirror = a_mirror(
            url,
            directory=tmp_path,
            credential=_A_STORED_REFERENCE,
            resolver=FixedSecret(""),
        )
        connection = mirror.connect()

    assert connection.state is ConnectionState.UNREACHABLE
    assert connection.revision is None


@_DEMANDING_HOSTS
def test_a_check_without_a_secret_meets_a_demanded_login_as_unreachable(
    handler_class: type[RespondingHandler],
) -> None:
    with responding_remote(
        status=HTTPStatus.UNAUTHORIZED, handler_class=handler_class
    ) as url:
        checked = check_connection(
            url=url,
            ref=MAIN_BRANCH,
            secret=None,
            timeout=A_GENEROUS_BOUND,
        )

    assert checked.state is ConnectionState.UNREACHABLE
    assert checked.commit is None
