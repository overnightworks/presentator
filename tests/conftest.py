"""The fixtures every layer shares."""

import os
import socket
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from tests.support.git_remote import GitRemote, a_remote
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    AN_AGENT_SERVICE,
    INSTANCE_KEY,
    configure_oidc,
    configure_secret_key,
)
from tests.support.worker_support import WAIT_SECONDS, listening_socket


@pytest.fixture
def remote(tmp_path: Path) -> GitRemote:
    """An empty bare repository with a working tree that pushes into it."""
    return a_remote(tmp_path, "remote")


@pytest.fixture
def another_remote(tmp_path: Path) -> GitRemote:
    """A second one, for what two sources of one instance do to one another."""
    return a_remote(tmp_path, "another-remote")


@pytest.fixture
def listener() -> Iterator[socket.socket]:
    """A listening socket for the worker's phases, closed at teardown."""
    with listening_socket() as listening:
        listening.settimeout(WAIT_SECONDS)
        yield listening


@pytest.fixture
def connection() -> Iterator[socket.socket]:
    """The controller's end of an accepted connection, with its client kept open."""
    controller_end, client = socket.socketpair()
    controller_end.settimeout(WAIT_SECONDS)
    client.settimeout(WAIT_SECONDS)
    with closing(controller_end), closing(client):
        yield controller_end


@pytest.fixture
def volume(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    before = os.umask(0)
    os.umask(before)
    directory = tmp_path_factory.mktemp("v")
    directory.chmod(0o700)
    yield directory
    os.umask(before)


@pytest.fixture
def wire() -> Iterator[tuple[socket.socket, socket.socket]]:
    """A connected socket pair whose every wait is bounded."""
    reading_end, writing_end = socket.socketpair()
    reading_end.settimeout(WAIT_SECONDS)
    writing_end.settimeout(WAIT_SECONDS)
    with closing(reading_end), closing(writing_end):
        yield reading_end, writing_end


@pytest.fixture
def deck(tmp_path: Path) -> Iterator[int]:
    """An open descriptor on an empty scratch deck, closed at the end."""
    (tmp_path / "deck").mkdir()
    descriptor = os.open(tmp_path / "deck", os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@pytest.fixture
def bare_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> pytest.MonkeyPatch:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PRESENTATOR_SECRET_KEY_FILE", raising=False)
    monkeypatch.setenv("PRESENTATOR_DATABASE", str(tmp_path / "presentator.sqlite3"))
    # `remote`/`another_remote` write their bare
    # repositories straight into `tmp_path`, the one directory every real
    # git address these tests build stands under; naming it as the mount
    # lets a `file://` fixture keep standing in for "any git remote" without
    # every such test naming the mount for itself.
    monkeypatch.setenv("PRESENTATOR_LOCAL_SOURCES_MOUNT", str(tmp_path))
    monkeypatch.setenv("PRESENTATOR_PUBLIC_ORIGIN", A_BROWSERS_ADDRESS)
    configure_oidc(monkeypatch, tmp_path)
    monkeypatch.setenv("PRESENTATOR_AGENT_SERVICE", AN_AGENT_SERVICE)
    return monkeypatch


@pytest.fixture
def environment(
    bare_environment: pytest.MonkeyPatch, tmp_path: Path
) -> pytest.MonkeyPatch:
    configure_secret_key(bare_environment, tmp_path, INSTANCE_KEY)
    # These tests are about the lobby this root composes, not about where a
    # deck's code runs; the development run is what asks nothing of the build
    # worker, whose default the tests about the sandbox say so themselves.
    bare_environment.setenv("PRESENTATOR_BUILD_RUNNER", "host")
    return bare_environment
