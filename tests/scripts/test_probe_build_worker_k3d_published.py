"""The k3d driver's published-image and Redis sources, driven through a fake runner."""

from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from tests.scripts.support.k3d_world import (
    BROKEN_FORMS,
    OTHER_DIGEST,
    Fixture,
    World,
    command_positions,
    fixture_maker,
    issued_commands,
)


@pytest.fixture
def driver_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d")


@pytest.fixture
def make_fixture(
    load_script: Callable[..., ModuleType], tmp_path: Path
) -> Callable[..., Fixture]:
    return fixture_maker(load_script, tmp_path)


@pytest.fixture
def manifests_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d_manifests")


def recorded_drive() -> tuple[
    list[tuple[Path, object]], Callable[[Path, object], None]
]:
    """The drives a `main` asked for, and the drive that records them."""
    driven: list[tuple[Path, object]] = []

    def record(checkout: Path, released: object) -> None:
        driven.append((checkout, released))

    return driven, record


@pytest.mark.parametrize(
    "break_form",
    [broken for _, broken in BROKEN_FORMS],
    ids=[name for name, _ in BROKEN_FORMS],
)
def test_a_published_image_of_another_form_is_refused_before_the_drive_starts(
    make_fixture: Callable[..., Fixture],
    driver_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    break_form: Callable[[str], str],
) -> None:
    reference = make_fixture().reference
    malformed = break_form(reference)
    assert malformed != reference
    driven, record = recorded_drive()
    with pytest.raises(SystemExit):
        driver_module.main(["drive", "--platform", "any", "--image", malformed], record)
    assert "@sha256:<64 lowercase hex>" in capsys.readouterr().err
    assert driven == []


def test_a_published_image_of_the_registry_form_starts_the_drive_over_its_digest(
    make_fixture: Callable[..., Fixture], driver_module: ModuleType
) -> None:
    reference = make_fixture().reference
    driven, record = recorded_drive()
    driver_module.main(["drive", "--platform", "any", "--image", reference], record)
    assert driven == [(Path("any"), driver_module.released_image(reference))]


@pytest.mark.parametrize(
    "arguments",
    [
        ["drive", "--platform", "any", "--image"],
        ["drive", "--image", "x", "--platform", "any"],
        ["drive", "--platform", "any", "--image", "x", "--image", "y"],
    ],
    ids=["no reference", "other order", "twice"],
)
def test_the_driver_gives_its_usage_for_the_image_argument_it_does_not_know(
    driver_module: ModuleType, arguments: list[str]
) -> None:
    with pytest.raises(SystemExit) as ended:
        driver_module.main(arguments)
    assert "drive --platform CHECKOUT [--image REF]" in str(ended.value.code)


def test_a_drive_over_the_published_image_pulls_by_digest_and_proves_it_first(
    make_fixture: Callable[..., Fixture], capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = make_fixture()
    fixture.drive(released=True)
    pinned, tag = fixture.world.production_image, fixture.run.tag
    steps = [
        ("docker", "pull", pinned),
        ("docker", "tag", pinned, tag),
        ("docker", "image", "inspect", "--format", "{{json .RepoDigests}}", tag),
        ("docker", "image", "rm", pinned),
        ("k3d", "cluster", "create"),
    ]
    positions = [command_positions(fixture.world, *step) for step in steps]
    assert all(len(found) == 1 for found in positions)
    assert positions == sorted(positions)
    assert command_positions(fixture.world, "docker", "build") == []
    lines = capsys.readouterr().out.splitlines()
    said = f"released image {fixture.reference} is {tag}, repo digest {pinned}"
    assert lines.index(said) < next(
        i for i, line in enumerate(lines) if line.startswith("identity holds")
    )


@pytest.mark.parametrize(
    "digests", [(), (OTHER_DIGEST,)], ids=["none", "another digest"]
)
def test_a_tag_whose_repo_digests_lack_the_published_digest_is_refused_then_torn_down(
    make_fixture: Callable[..., Fixture],
    manifests_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    digests: tuple[str, ...],
) -> None:
    listed = [f"{manifests_module.PRODUCTION_IMAGE}{digest}" for digest in digests]
    fixture = make_fixture(World(repo_digests=listed))
    with pytest.raises(SystemExit):
        fixture.drive(released=True)
    refusal = capsys.readouterr().err
    assert fixture.world.production_image in refusal
    assert all(digest in refusal for digest in listed)
    assert fixture.world.issued("k3d", "cluster", "create") == []
    assert fixture.world.issued("docker", "image", "rm", "--force") != []
    assert not fixture.run.directory.exists()


def test_a_pull_that_fails_refuses_with_its_error_then_tears_down(
    make_fixture: Callable[..., Fixture], capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = make_fixture(World(pull_error="denied: permission_denied"))
    with pytest.raises(SystemExit):
        fixture.drive(released=True)
    assert "denied: permission_denied" in capsys.readouterr().err
    assert fixture.world.issued("docker", "tag") == []
    assert fixture.world.issued("k3d", "cluster", "create") == []
    assert fixture.world.issued("k3d", "cluster", "delete") != []
    assert not fixture.run.directory.exists()


def test_the_probe_redis_is_pulled_by_the_runner_and_imported_before_the_pod_exists(
    make_fixture: Callable[..., Fixture], manifests_module: ModuleType
) -> None:
    fixture = make_fixture()
    fixture.drive()
    pinned, local = manifests_module.REDIS_PINNED, fixture.run.redis_image
    steps = [
        ("docker", "pull", pinned),
        ("docker", "tag", pinned, local),
        ("k3d", "cluster", "create"),
        ("k3d", "image", "import", local),
        ("kubectl", "apply"),
    ]
    positions = [command_positions(fixture.world, *step) for step in steps]
    assert all(found for found in positions)
    assert [found[0] for found in positions] == sorted(found[0] for found in positions)
    assert "@sha256:" in pinned


def test_a_redis_pull_that_fails_names_the_image_and_creates_no_cluster(
    make_fixture: Callable[..., Fixture],
    manifests_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = make_fixture(World(pull_error="429 Too Many Requests"))
    with pytest.raises(SystemExit):
        fixture.drive()
    refusal = capsys.readouterr().err
    assert manifests_module.REDIS_PINNED in refusal
    assert "429 Too Many Requests" in refusal
    assert fixture.world.issued("k3d", "cluster", "create") == []


def test_a_redis_import_that_fails_names_the_image_then_tears_down(
    make_fixture: Callable[..., Fixture],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = make_fixture(World(redis_import_error="no space left"))
    with pytest.raises(SystemExit):
        fixture.drive()
    refusal = capsys.readouterr().err
    assert fixture.run.redis_image in refusal
    assert "no space left" in refusal
    assert fixture.world.issued("kubectl", "apply") == []
    assert fixture.world.issued("k3d", "cluster", "delete") != []


@pytest.mark.parametrize(
    ("found", "cells_printed"),
    [(0, {"5"}), (1, {"5", "7", "8", "9"})],
    ids=["right after its import", "when the web cells would apply it"],
)
def test_a_redis_image_the_node_lacks_is_named_and_never_applied(
    make_fixture: Callable[..., Fixture],
    manifests_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    found: int,
    cells_printed: set[str],
) -> None:
    fixture = make_fixture(World(redis_found_in_node=found))
    with pytest.raises(SystemExit):
        fixture.drive()
    output = capsys.readouterr()
    assert fixture.run.redis_image in output.err
    assert "is not in the cluster's node" in output.err
    printed = {
        line.split(":")[0].removeprefix("cell ")
        for line in output.out.splitlines()
        if line.startswith("cell ")
    }
    assert printed == cells_printed
    applied = [d["metadata"]["name"] for d in fixture.world.applied("Deployment")]
    assert manifests_module.REDIS not in applied
    assert fixture.world.issued("k3d", "cluster", "delete") != []


@pytest.mark.parametrize(
    "failing", [("k3d", "cluster", "create"), ("k3d", "image", "import")]
)
def test_a_probe_ended_after_the_redis_pull_leaves_no_redis_image(
    make_fixture: Callable[..., Fixture],
    driver_module: ModuleType,
    failing: tuple[str, ...],
) -> None:
    fixture = make_fixture(World(failing_command=failing))
    with pytest.raises(RuntimeError):
        fixture.drive()
    assert not fixture.world.redis_image_present
    driver_module.check_leftovers(fixture.world.lister)


def test_the_cells_and_the_commands_from_the_cluster_on_are_the_same_for_either_source(
    make_fixture: Callable[..., Fixture], capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[tuple[list[str], list[tuple[tuple[str, ...], str]]]] = []
    for released in (False, True):
        fixture = make_fixture()
        fixture.drive(released=released)
        lines = capsys.readouterr().out.splitlines()
        start = command_positions(fixture.world, "k3d", "cluster", "create")[0]
        shown = [line for line in lines if not line.startswith("released image")]
        seen.append((shown, issued_commands(fixture.world)[start:]))
    built, published = seen
    assert built == published
