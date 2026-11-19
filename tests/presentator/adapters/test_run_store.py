"""The runs in the object store, through the real client against the run's Garage."""

from pathlib import Path

import pytest

from presentator.adapters.run_store import StoredRuns, object_store_client
from presentator.contracts.decks import BuildStoreUnavailableError
from tests.support.log_lines import events_of, json_logged
from tests.support.object_store import ObjectStore, no_object_store_environment

_FAILED = "object_store.failed"


def a_run(directory: Path, name: str) -> tuple[Path, Path]:
    """An archive and a PDF of that run, each holding its own bytes."""
    directory.mkdir(parents=True)
    archive, pdf = directory / "talk.tar", directory / "deck.pdf"
    archive.write_bytes(f"archive of {name}".encode())
    pdf.write_bytes(f"pdf of {name}".encode())
    return archive, pdf


def test_a_kept_run_is_listed_fetched_whole_and_deleted(
    tmp_path: Path, object_store: ObjectStore
) -> None:
    runs = object_store.runs
    archive, pdf = a_run(tmp_path / "put", "run-1")

    assert runs.put("run-1", archive=archive, pdf=pdf)
    assert runs.names() == frozenset({"run-1"})
    fetched = tmp_path / "fetched"
    fetched.mkdir()
    assert runs.fetch("run-1", archive=fetched / "talk.tar", pdf=fetched / "deck.pdf")
    assert (fetched / "talk.tar").read_bytes() == archive.read_bytes()
    assert (fetched / "deck.pdf").read_bytes() == pdf.read_bytes()

    assert runs.delete("run-1")
    assert runs.names() == frozenset()


def test_a_run_the_store_does_not_hold_is_not_fetched_and_no_failure(
    tmp_path: Path, object_store: ObjectStore, capsys: pytest.CaptureFixture[str]
) -> None:
    with json_logged(capsys) as logged:
        fetched = object_store.runs.fetch(
            "never-kept", archive=tmp_path / "talk.tar", pdf=tmp_path / "deck.pdf"
        )

    assert not fetched
    assert events_of(logged, _FAILED) == []


def test_a_store_that_refuses_the_key_fails_every_call_with_one_line_each(
    tmp_path: Path,
    bare_environment: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name, value in no_object_store_environment(tmp_path).items():
        bare_environment.setenv(name, value)
    runs = StoredRuns(client=object_store_client(), bucket="no-bucket-here")
    archive, pdf = a_run(tmp_path / "put", "run-1")

    with json_logged(capsys) as logged:
        put = runs.put("run-1", archive=archive, pdf=pdf)
        with pytest.raises(BuildStoreUnavailableError):
            runs.fetch("run-1", archive=tmp_path / "a.tar", pdf=tmp_path / "a.pdf")
        answers = (put, runs.names(), runs.delete("run-1"))

    assert answers == (False, None, False)
    failed = events_of(logged, _FAILED)
    assert [line["operation"] for line in failed] == ["put", "fetch", "list", "delete"]


def test_a_client_reaches_the_store_its_environment_names_when_it_is_made(
    tmp_path: Path, object_store: ObjectStore, bare_environment: pytest.MonkeyPatch
) -> None:
    for name, value in no_object_store_environment(tmp_path).items():
        bare_environment.setenv(name, value)
    refused = StoredRuns(client=object_store_client(), bucket=object_store.runs.bucket)

    assert refused.names() is None


def test_a_key_the_store_does_not_know_makes_a_fetch_unavailable_with_one_line(
    tmp_path: Path,
    object_store: ObjectStore,
    bare_environment: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    unknown = tmp_path / "an-unknown-key"
    unknown.write_text(
        f"[default]\naws_access_key_id = GK{'0' * 24}\n"
        f"aws_secret_access_key = {'0' * 64}\n",
        encoding="utf-8",
    )
    bare_environment.setenv("AWS_SHARED_CREDENTIALS_FILE", str(unknown))
    refused = StoredRuns(client=object_store_client(), bucket=object_store.runs.bucket)

    with json_logged(capsys) as logged, pytest.raises(BuildStoreUnavailableError):
        refused.fetch("run-1", archive=tmp_path / "talk.tar", pdf=tmp_path / "deck.pdf")

    assert [line["operation"] for line in events_of(logged, _FAILED)] == ["fetch"]


@pytest.mark.usefixtures("object_store")
def test_a_bucket_the_store_does_not_hold_fails_a_put_with_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    refused = StoredRuns(client=object_store_client(), bucket="no-such-bucket-here")
    archive, pdf = a_run(tmp_path / "put", "run-1")

    with json_logged(capsys) as logged:
        taken = refused.put("run-1", archive=archive, pdf=pdf)

    assert not taken
    assert [line["operation"] for line in events_of(logged, _FAILED)] == ["put"]
