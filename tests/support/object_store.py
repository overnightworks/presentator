"""The Garage a test run is given, and the environment of an instance without one.

CI starts Garage with platform's action and a developer starts the same image;
either exports the variables boto3 reads and names the bucket in
`PRESENTATOR_TEST_S3_BUCKET`. The one bucket is shared, and a sweep deletes
every run its own database names nowhere, so every test that asks for the
`object_store` fixture holds the bucket alone, across every xdist worker of the
run, and starts from an emptied bucket.
"""

import fcntl
import os
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from presentator.adapters.run_store import StoredRuns, object_store_client

BUCKET_VARIABLE: Final = "PRESENTATOR_TEST_S3_BUCKET"
_STORE_VARIABLES: Final = (
    "AWS_ENDPOINT_URL_S3",
    "AWS_DEFAULT_REGION",
    "AWS_SHARED_CREDENTIALS_FILE",
)
_NO_STORE: Final = (
    f"{BUCKET_VARIABLE} and {', '.join(_STORE_VARIABLES)} name no Garage: start"
    " one and set them to a bucket whose objects a test may delete"
)
# Read before any fixture points an instance's environment elsewhere.
_GIVEN: Final = {
    name: os.environ.get(name, "") for name in (*_STORE_VARIABLES, BUCKET_VARIABLE)
}


def object_store_environment() -> dict[str, str]:
    """What an instance reads to reach the test run's Garage and its bucket."""
    if not all(_GIVEN.values()):
        pytest.fail(_NO_STORE, pytrace=False)
    return {
        **{name: _GIVEN[name] for name in _STORE_VARIABLES},
        "PRESENTATOR_S3_BUCKET": _GIVEN[BUCKET_VARIABLE],
    }


def no_object_store_environment(directory: Path) -> Mapping[str, str]:
    """An instance whose store refuses at once, without asking any network.

    No key file holds a key and no machine metadata is asked for one, so every
    call fails before it is sent.
    """
    nothing = directory / "no-object-store-key"
    return {
        "AWS_SHARED_CREDENTIALS_FILE": str(nothing),
        "AWS_CONFIG_FILE": str(nothing),
        "AWS_EC2_METADATA_DISABLED": "true",
    }


@dataclass(frozen=True, slots=True)
class ObjectStore:
    """The test run's bucket, emptied, and what a process reads to reach it."""

    runs: StoredRuns
    environment: Mapping[str, str]


@contextmanager
def the_bucket_alone(shared: Path) -> Generator[None]:
    """Hold the bucket for this test alone among all that lock in `shared`."""
    with (shared / "object-store.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def emptied_store(environment: pytest.MonkeyPatch) -> ObjectStore:
    """The runs of the test run's bucket through the real adapter, none left.

    This process reads the store from `environment`, as an instance does; a
    child process is handed `ObjectStore.environment`.
    """
    given = object_store_environment()
    for name, value in given.items():
        environment.setenv(name, value)
    for name in ("AWS_CONFIG_FILE", "AWS_EC2_METADATA_DISABLED"):
        environment.delenv(name, raising=False)
    runs = StoredRuns(
        client=object_store_client(), bucket=given["PRESENTATOR_S3_BUCKET"]
    )
    listed = runs.names()
    assert listed is not None
    for name in listed:
        assert runs.delete(name)
    return ObjectStore(runs=runs, environment=given)
