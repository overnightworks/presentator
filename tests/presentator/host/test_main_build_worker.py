"""The composition root builds a deck through the build worker when told to."""

import os
import re
import socket
from pathlib import Path

import pytest

from presentator.adapters.build_wire import SOCKET_NAME
from presentator.adapters.build_worker import WorkerToolchain
from presentator.adapters.builds import DeckToolchain, SlidevBuilds
from presentator.adapters.decks import SourceMirrors
from presentator.adapters.toolchain_identity import toolchain_identity_of
from presentator.contracts.decks import ToolchainIdentity
from presentator.host import main
from presentator.host.config import ConfigurationError, load_settings
from presentator.host.worker_serving import emergency_end

BUILD_TIMEOUT_SECONDS = 123.0
OUTPUT_MEGABYTES = 77


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


@pytest.mark.parametrize("runner", ["host", "worker"])
def test_the_runner_an_instance_builds_with_answers_the_identity_of_its_project(
    environment: pytest.MonkeyPatch, tmp_path: Path, runner: str
) -> None:
    project = tmp_path / "a-toolchain-project"
    project.mkdir()
    (project / "package.json").write_text("{}", encoding="utf-8")
    environment.setenv("PRESENTATOR_BUILD_RUNNER", runner)
    environment.setenv("PRESENTATOR_TOOLCHAIN", str(project))

    def answering(_toolchain: WorkerToolchain) -> bool:
        return True

    environment.setattr(WorkerToolchain, "is_answering", answering)
    composed: list[SlidevBuilds] = []

    def composing(
        *,
        builds: Path,
        identity: ToolchainIdentity,
        toolchain: DeckToolchain,
        mirrors: SourceMirrors,
        output_megabytes: int,
    ) -> SlidevBuilds:
        composed.append(
            SlidevBuilds(
                builds=builds,
                identity=identity,
                toolchain=toolchain,
                mirrors=mirrors,
                output_megabytes=output_megabytes,
            )
        )
        return composed[-1]

    environment.setattr(main, "SlidevBuilds", composing)

    main.build_instance(load_settings())

    assert [runner.toolchain_identity() for runner in composed] == [
        toolchain_identity_of(project)
    ]


@pytest.mark.parametrize("runner", ["host", "worker"])
def test_an_instance_whose_toolchain_project_cannot_be_read_refuses_naming_it(
    environment: pytest.MonkeyPatch, tmp_path: Path, runner: str
) -> None:
    project = tmp_path / "no-toolchain-here"
    environment.setenv("PRESENTATOR_BUILD_RUNNER", runner)
    environment.setenv("PRESENTATOR_TOOLCHAIN", str(project))

    with pytest.raises(ConfigurationError, match=re.escape(str(project))):
        main.build_instance(load_settings())
