"""The composition root builds a deck through the runner it is told to."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from presentator.adapters.postgres.deck_store import PostgresDeckStore
from presentator.adapters.toolchain_identity import toolchain_identity_of
from presentator.contracts.models import Role, User
from presentator.host import main
from presentator.host.config import ConfigurationError, load_settings
from presentator.host.worker_serving import emergency_end
from tests.support.git_remote import GitRemote
from tests.support.people import seed_user
from tests.support.polled_source_support import add_a_polled_source
from tests.support.postgres import NO_FENCE
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
    toolchain = main.the_build_worker(settings, bound=main.build_bound_of(settings))

    assert (toolchain.host, toolchain.port) == ("build-worker", 7100)
    assert toolchain.key == KEY
    assert toolchain.megabytes == OUTPUT_MEGABYTES
    assert toolchain.reply_bound_seconds == emergency_end(0.0, BUILD_TIMEOUT_SECONDS)
    assert KEY.decode() not in repr(toolchain)


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
    bound = main.build_bound_of(settings)

    with pytest.raises(ConfigurationError, match="build_worker_address"):
        main.the_build_worker(settings, bound=bound)


@pytest.mark.usefixtures("served_database")
def test_a_key_file_that_is_too_short_refuses_to_start(
    environment: pytest.MonkeyPatch, key_file: Path
) -> None:
    key_file.write_text("short")
    a_worker_run(environment, key_file)

    with pytest.raises(ConfigurationError, match="build_worker_key_file"):
        main.build_instance(load_settings())


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
