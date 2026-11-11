"""The composition root builds a deck through the build worker when told to."""

import asyncio
import os
import re
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest

from presentator.adapters.build_wire import SOCKET_NAME
from presentator.adapters.build_worker import WorkerToolchain
from presentator.adapters.deck_store import SqliteDeckStore
from presentator.adapters.decks import SqliteSourceStore
from presentator.adapters.identity import TokenIdentifierFactory
from presentator.adapters.secrets import secret_box
from presentator.adapters.toolchain_identity import toolchain_identity_of
from presentator.application.decks import hash_webhook_secret
from presentator.contracts.decks import SourceWrite
from presentator.contracts.models import Role, User
from presentator.host import main
from presentator.host.config import ConfigurationError, load_settings
from presentator.host.worker_serving import emergency_end
from tests.support.git_remote import GitRemote
from tests.support.lobby import INSTANCE_KEY
from tests.support.people import seed_user

BUILD_TIMEOUT_SECONDS = 123.0
OUTPUT_MEGABYTES = 77
WHAT_THE_GIT_HOST_EXPECTS = "the words only this test made up"
AN_OWNER = User(id="id-of-an-owner", username="an-owner", role=Role.ADMIN)


def a_worker_run(environment: pytest.MonkeyPatch, socket_path: Path) -> None:
    environment.setenv("PRESENTATOR_BUILD_RUNNER", "worker")
    environment.setenv("PRESENTATOR_BUILD_WORKER_SOCKET", str(socket_path))
    environment.setenv("PRESENTATOR_BUILD_TIMEOUT_SECONDS", str(BUILD_TIMEOUT_SECONDS))
    environment.setenv("PRESENTATOR_BUILD_OUTPUT_MEGABYTES", str(OUTPUT_MEGABYTES))


def test_an_instance_whose_worker_socket_is_not_the_workers_refuses_to_start_at_once(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cell B14: the branch of the root, reached without the connect window."""
    volume = tmp_path / "volume"
    volume.mkdir(mode=0o700)
    placeholder = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    placeholder.bind(str(volume / SOCKET_NAME))
    a_worker_run(environment, volume / SOCKET_NAME)

    settings = load_settings()

    with placeholder, pytest.raises(ConfigurationError, match="build worker"):
        main.build_instance(settings)


def test_an_instance_starts_on_a_worker_that_answers_with_the_workers_own_bounds(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Review need Q4: the seam fields reach the client as one production value."""
    built: list[WorkerToolchain] = []

    def answering(toolchain: WorkerToolchain) -> bool:
        built.append(toolchain)
        return True

    a_worker_run(environment, tmp_path / "worker.sock")
    environment.setattr(WorkerToolchain, "is_answering", answering)

    main.build_instance(load_settings())

    (toolchain,) = built
    assert toolchain.socket_path == tmp_path / "worker.sock"
    assert toolchain.megabytes == OUTPUT_MEGABYTES
    assert toolchain.reply_bound_seconds == emergency_end(0.0, BUILD_TIMEOUT_SECONDS)
    assert (toolchain.expected_uid, toolchain.expected_gid) == (0, os.getegid())


def test_a_deck_the_host_runner_builds_records_the_identity_of_the_toolchain_project(
    environment: pytest.MonkeyPatch, remote: GitRemote
) -> None:
    environment.setenv("PRESENTATOR_BUILD_RUNNER", "host")
    settings = load_settings()
    instance = main.build_instance(settings)
    seed_user(settings.database, AN_OWNER)
    added = SqliteSourceStore(
        database=settings.database,
        identifiers=TokenIdentifierFactory(),
        box=secret_box(INSTANCE_KEY),
    ).add(
        SourceWrite(
            name="talks",
            url=remote.url,
            ref="main",
            owner_id=AN_OWNER.id,
            access_secret=WHAT_THE_GIT_HOST_EXPECTS,
            hook_secret_hash=hash_webhook_secret(WHAT_THE_GIT_HOST_EXPECTS),
        )
    )
    assert added is not None
    remote.commit_example_deck(at=datetime(2026, 1, 15, 9, tzinfo=UTC))

    asyncio.run(instance.poller.tick())

    (kept,) = SqliteDeckStore(database=settings.database).all()
    assert kept.attempt is not None
    assert kept.attempt.toolchain == toolchain_identity_of(settings.toolchain)


@pytest.mark.parametrize("runner", ["host", "worker"])
def test_an_instance_whose_toolchain_project_cannot_be_read_refuses_naming_it(
    environment: pytest.MonkeyPatch, tmp_path: Path, runner: str
) -> None:
    project = tmp_path / "no-toolchain-here"
    environment.setenv("PRESENTATOR_BUILD_RUNNER", runner)
    environment.setenv("PRESENTATOR_TOOLCHAIN", str(project))

    with pytest.raises(ConfigurationError, match=re.escape(str(project))):
        main.build_instance(load_settings())
