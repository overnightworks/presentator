"""The fixtures every layer shares."""

import logging
import os
import socket
from collections.abc import Callable, Iterator
from contextlib import closing
from functools import partial
from pathlib import Path

import pytest
import uvicorn
from logline.testing import configured
from sqlalchemy import Engine

from presentator.api.health import Stopping
from presentator.host import main
from tests.support.git_remote import (
    GitRemote,
    a_remote,
    reading_fixture_remotes,
)
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_GATEWAY_KEY_FILE,
    A_GATEWAY_ORIGIN,
    A_SPEECH_SCOPE,
    A_SPEECH_SERVICE,
    AN_API_GROUP,
    INSTANCE_KEY,
    LOBBY_BUILD,
    configure_oidc,
    configure_secret_key,
)
from tests.support.log_lines import LoggedText
from tests.support.postgres import (
    NO_DATABASE_HERE,
    created_database,
    database_environment,
    engine_on,
    migrated_template,
)
from tests.support.redis import NOTHING_LISTENS_HERE, emptied_database_url
from tests.support.worker_support import (
    KEY,
    WAIT_SECONDS,
    free_port,
    listening_socket,
)


@pytest.fixture
def json_logging() -> Iterator[None]:
    """Undo the logging a test's in-process `main()` configures, as it was before.

    `main()` also caps alembic's logger, which logline's undo does not know.
    """
    alembic = logging.getLogger("alembic")
    level = alembic.level
    with configured("test"):
        yield
    alembic.setLevel(level)


@pytest.fixture
def logged(json_logging: None, capsys: pytest.CaptureFixture[str]) -> LoggedText:
    """What a test's in-process `main()` writes to the log, read as it goes."""
    del json_logging
    return LoggedText(capsys)


class _ServerThatOnlyReturns:
    def __init__(self, config: uvicorn.Config, stopping: Stopping) -> None:
        del config, stopping

    async def serve(self) -> None:
        return None


@pytest.fixture
def main_without_serving(environment: pytest.MonkeyPatch) -> Callable[[], None]:
    """`main()` as far as serving, with a server that returns at once."""
    environment.setattr(main, "StoppingServer", _ServerThatOnlyReturns)
    return partial(main.main, [])


@pytest.fixture
def remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitRemote:
    """An empty bare repository with a working tree that pushes into it."""
    reading_fixture_remotes(monkeypatch)
    return a_remote(tmp_path, "remote")


@pytest.fixture
def another_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitRemote:
    """A second one, for what two sources of one instance do to one another."""
    reading_fixture_remotes(monkeypatch)
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
    (directory / "port").write_text(str(free_port()))
    (directory / "key").write_bytes(KEY)
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
    # Nothing listens at this database; the tests that serve name their own.
    for name, value in database_environment(NO_DATABASE_HERE, tmp_path).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PRESENTATOR_LOBBY_BUILD", str(LOBBY_BUILD))
    toolchain = tmp_path / "toolchain"
    toolchain.mkdir()
    (toolchain / "package.json").write_text("{}")
    monkeypatch.setenv("PRESENTATOR_TOOLCHAIN", str(toolchain))
    monkeypatch.setenv("PRESENTATOR_PUBLIC_ORIGIN", A_BROWSERS_ADDRESS)
    monkeypatch.setenv("PRESENTATOR_REDIS_URL", NOTHING_LISTENS_HERE)
    configure_oidc(monkeypatch, tmp_path)
    monkeypatch.setenv("PRESENTATOR_AGENT__BACKEND", "litellm")
    monkeypatch.setenv("PRESENTATOR_AGENT__GATEWAY__ORIGIN", A_GATEWAY_ORIGIN)
    monkeypatch.setenv("PRESENTATOR_AGENT__GATEWAY__KEY_FILE", str(A_GATEWAY_KEY_FILE))
    monkeypatch.setenv("PRESENTATOR_AGENT__API_GROUP", AN_API_GROUP)
    monkeypatch.setenv("PRESENTATOR_SPEECH_SERVICE", A_SPEECH_SERVICE)
    monkeypatch.setenv("PRESENTATOR_SPEECH_SERVICE_SCOPE", A_SPEECH_SCOPE)
    # These tests are about the lobby this root composes, not about where a
    # deck's code runs; the development run is what asks nothing of the build
    # worker, whose default the tests about the sandbox say so themselves.
    monkeypatch.setenv("PRESENTATOR_BUILD_RUNNER", "host")
    return monkeypatch


@pytest.fixture
def environment(
    bare_environment: pytest.MonkeyPatch, tmp_path: Path
) -> pytest.MonkeyPatch:
    configure_secret_key(bare_environment, tmp_path, INSTANCE_KEY)
    return bare_environment


@pytest.fixture(scope="session")
def postgres_template() -> Iterator[str]:
    """The name of a migrated database, created by the first test that asks."""
    with migrated_template() as name:
        yield name


@pytest.fixture
def database_url(postgres_template: str) -> Iterator[str]:
    """A migrated database of this test's own."""
    with created_database(postgres_template) as url:
        yield url


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    """An engine on this test's own database, disposed at teardown."""
    each = engine_on(database_url)
    try:
        yield each
    finally:
        each.dispose()


@pytest.fixture
def served_database(
    bare_environment: pytest.MonkeyPatch, database_url: str, tmp_path: Path
) -> str:
    """An instance that serves from a migrated database of this test's own."""
    for name, value in database_environment(database_url, tmp_path).items():
        bare_environment.setenv(name, value)
    return database_url


@pytest.fixture
def unmigrated_database_url() -> Iterator[str]:
    """An empty database of this test's own."""
    with created_database() as url:
        yield url


@pytest.fixture
def redis_url() -> str:
    """The URL of a Redis database of this worker's own, emptied for this test."""
    return emptied_database_url()


@pytest.fixture
def redis_environment(
    bare_environment: pytest.MonkeyPatch, redis_url: str
) -> pytest.MonkeyPatch:
    """An instance that spends its request budgets in a Redis of this test's own."""
    bare_environment.setenv("PRESENTATOR_REDIS_URL", redis_url)
    return bare_environment
