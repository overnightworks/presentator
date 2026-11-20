"""The composition root builds a deck through the runner it is told to."""

import asyncio
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from presentator.adapters.postgres.deck_store import PostgresDeckStore
from presentator.adapters.toolchain_identity import toolchain_identity_of
from presentator.contracts.models import Role, User
from presentator.host import main, worker
from presentator.host.config import load_settings
from presentator.host.deck_toolchain import build_bound_of, the_build_worker
from presentator.host.environment import ConfigurationError
from presentator.host.worker_serving import emergency_end
from tests.support.git_remote import GitRemote
from tests.support.people import seed_user
from tests.support.polled_source_support import add_a_polled_source
from tests.support.postgres import NO_FENCE, other_connections
from tests.support.worker_support import KEY

BUILD_TIMEOUT_SECONDS = 123.0
OUTPUT_MEGABYTES = 77
WORKER_ADDRESS = "build-worker:7100"
WHAT_THE_GIT_HOST_EXPECTS = "the words only this test made up"
AN_OWNER = User(id="id-of-an-owner", username="an-owner", role=Role.ADMIN)


def a_worker_run(environment: pytest.MonkeyPatch, key_file: Path) -> None:
    environment.setenv("PRESENTATOR_BUILD_RUNNER", "worker")
    environment.setenv("PRESENTATOR_BUILD_WORKER_ADDRESS", WORKER_ADDRESS)
    environment.setenv("PRESENTATOR_BUILD_WORKER_KEY_FILE", str(key_file))
    environment.setenv("PRESENTATOR_BUILD_TIMEOUT_SECONDS", str(BUILD_TIMEOUT_SECONDS))
    environment.setenv("PRESENTATOR_BUILD_OUTPUT_MEGABYTES", str(OUTPUT_MEGABYTES))


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "build-worker-key"
    path.write_text(KEY.decode() + "\n")
    return path


@pytest.mark.usefixtures("served_database")
def test_an_instance_starts_with_no_worker_answering_and_holds_the_workers_bounds(
    environment: pytest.MonkeyPatch, key_file: Path
) -> None:
    """The client carries the address, the key and the bounds as one value."""
    a_worker_run(environment, key_file)
    settings = load_settings()

    main.build_instance(settings)
    toolchain = the_build_worker(settings, bound=build_bound_of(settings))

    assert (toolchain.host, toolchain.port) == ("build-worker", 7100)
    assert toolchain.key == KEY
    assert toolchain.megabytes == OUTPUT_MEGABYTES
    assert toolchain.reply_bound_seconds == emergency_end(0.0, BUILD_TIMEOUT_SECONDS)
    assert KEY.decode() not in repr(toolchain)


def test_the_instances_worker_client_reads_and_waits_on_the_process_monotonic_clock(
    environment: pytest.MonkeyPatch, key_file: Path
) -> None:
    a_worker_run(environment, key_file)
    settings = load_settings()
    client = the_build_worker(settings, bound=build_bound_of(settings))
    client_time = client.time
    a_short_wait = 0.001

    before = time.monotonic()
    read = client_time.now()
    after = time.monotonic()
    client_time.wait(a_short_wait)

    assert before <= read <= after
    assert time.monotonic() - after >= a_short_wait


@pytest.mark.parametrize(
    "missing", ["PRESENTATOR_BUILD_WORKER_ADDRESS", "PRESENTATOR_BUILD_WORKER_KEY_FILE"]
)
def test_a_worker_run_without_its_address_or_key_file_refuses_to_start(
    environment: pytest.MonkeyPatch, key_file: Path, missing: str
) -> None:
    a_worker_run(environment, key_file)
    environment.delenv(missing)

    with pytest.raises(ConfigurationError, match="build_worker"):
        load_settings()


VALID_HELLO_KEYS = {
    "32 bytes": b"k" * 32,
    "4096 bytes": b"k" * 4096,
    "sixteen two-byte characters": ("é" * 16).encode(),
    "2048 two-byte characters": ("é" * 2048).encode(),
}
REFUSED_HELLO_KEYS = {
    "31 bytes": b"k" * 31,
    "4097 bytes": b"k" * 4097,
    "fifteen two-byte characters": ("é" * 15).encode(),
    "2049 two-byte characters": ("é" * 2049).encode(),
}


def _web_reads(_key_file: Path) -> bytes:
    return load_settings().build_worker.key.get_secret_value().encode()


def _worker_reads(key_file: Path) -> bytes:
    return worker.read_key(key_file, controller_uid=os.getuid())


@pytest.mark.parametrize("read", [_web_reads, _worker_reads])
@pytest.mark.parametrize("key", VALID_HELLO_KEYS.values(), ids=VALID_HELLO_KEYS.keys())
def test_the_web_and_the_worker_both_read_a_key_of_32_to_4096_bytes(
    environment: pytest.MonkeyPatch,
    key_file: Path,
    read: Callable[[Path], bytes],
    key: bytes,
) -> None:
    key_file.write_bytes(key)
    key_file.chmod(0o400)
    a_worker_run(environment, key_file)

    assert read(key_file) == key


@pytest.mark.parametrize("read", [_web_reads, _worker_reads])
@pytest.mark.parametrize(
    "key", REFUSED_HELLO_KEYS.values(), ids=REFUSED_HELLO_KEYS.keys()
)
def test_the_web_and_the_worker_both_refuse_a_key_outside_32_to_4096_bytes(
    environment: pytest.MonkeyPatch,
    key_file: Path,
    read: Callable[[Path], bytes],
    key: bytes,
) -> None:
    key_file.write_bytes(key)
    key_file.chmod(0o400)
    a_worker_run(environment, key_file)

    with pytest.raises((ConfigurationError, worker.StartRefusedError)):
        read(key_file)


@pytest.mark.parametrize(
    "address",
    [
        "build-worker",
        "build-worker:0",
        ":7100",
        "build-worker:http",
        "build-worker:99999",
    ],
)
def test_an_address_that_is_not_a_host_and_a_port_refuses_to_start(
    environment: pytest.MonkeyPatch, key_file: Path, address: str
) -> None:
    a_worker_run(environment, key_file)
    environment.setenv("PRESENTATOR_BUILD_WORKER_ADDRESS", address)

    with pytest.raises(ConfigurationError, match="build_worker_address"):
        load_settings()


@pytest.mark.usefixtures("environment")
def test_a_host_run_that_names_no_worker_has_no_worker_link() -> None:
    settings = load_settings()
    bound = build_bound_of(settings)

    with pytest.raises(ConfigurationError, match="build_worker_address"):
        the_build_worker(settings, bound=bound)


@pytest.mark.usefixtures("served_database")
def test_a_key_file_that_is_too_short_refuses_to_start_and_holds_no_connection(
    environment: pytest.MonkeyPatch, key_file: Path, engine: Engine
) -> None:
    key_file.write_text("short")
    a_worker_run(environment, key_file)

    with pytest.raises(ConfigurationError, match="build_worker_key_file"):
        main.build_instance(load_settings())

    assert other_connections(engine) == 0


@pytest.mark.usefixtures("environment", "served_database")
def test_a_deck_the_host_runner_builds_records_the_identity_of_the_toolchain_project(
    remote: GitRemote, engine: Engine
) -> None:
    settings = load_settings()
    instance = main.build_instance(settings)
    seed_user(engine, AN_OWNER)
    add_a_polled_source(
        engine,
        remote,
        name="talks",
        owner_id=AN_OWNER.id,
        access_secret=WHAT_THE_GIT_HOST_EXPECTS,
        hook_secret=WHAT_THE_GIT_HOST_EXPECTS,
    )
    remote.commit_example_deck(at=datetime(2026, 1, 15, 9, tzinfo=UTC))

    asyncio.run(instance.poller.tick())

    (kept,) = PostgresDeckStore(engine=engine, fence=NO_FENCE).all()
    assert kept.attempt is not None
    assert kept.attempt.toolchain == toolchain_identity_of(settings.toolchain)


@pytest.mark.parametrize("runner", ["host", "worker"])
def test_an_unreadable_toolchain_project_refuses_naming_the_setting_and_the_path(
    environment: pytest.MonkeyPatch, tmp_path: Path, key_file: Path, runner: str
) -> None:
    project = tmp_path / "no-toolchain-here"
    a_worker_run(environment, key_file)
    environment.setenv("PRESENTATOR_BUILD_RUNNER", runner)
    environment.setenv("PRESENTATOR_TOOLCHAIN", str(project))

    with pytest.raises(ConfigurationError) as refused:
        main.build_instance(load_settings())

    assert "PRESENTATOR_TOOLCHAIN" in str(refused.value)
    assert str(project) in str(refused.value)
