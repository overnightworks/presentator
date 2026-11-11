"""The k3d driver's render proof and the documents it applies."""

import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from tests.scripts.support.k3d_world import (
    DIGEST,
    PROJECTED_KEYS,
    PROJECTED_NAME,
    RUN_IDENTIFIER,
    Document,
    Fixture,
    fixture_maker,
    named,
    workload_documents,
)


@pytest.fixture
def cluster_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d_cluster")


@pytest.fixture
def manifests_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d_manifests")


@pytest.fixture
def production_image(manifests_module: ModuleType) -> str:
    return manifests_module.PRODUCTION_IMAGE + DIGEST


@pytest.fixture
def make_fixture(
    load_script: Callable[..., ModuleType], tmp_path: Path
) -> Callable[..., Fixture]:
    return fixture_maker(load_script, tmp_path)


def test_the_overlay_names_the_platform_app_by_a_path_relative_to_itself(
    make_fixture: Callable[..., Fixture],
) -> None:
    fixture = make_fixture()
    fixture.drive()
    (resource,) = json.loads(fixture.world.overlay_file)["resources"]
    overlay = fixture.run.directory / "overlay"
    assert not Path(resource).is_absolute()
    assert (overlay / resource).resolve() == (
        fixture.run.platform / "apps" / "presentator"
    ).resolve()


@pytest.mark.parametrize(
    ("cell", "init_container"), [(0, False), (1, True)], ids=["cell 7", "cell 8"]
)
def test_a_cell_applies_only_the_worker_pods_kinds_and_the_cell_dependent_fields_differ(
    make_fixture: Callable[..., Fixture],
    manifests_module: ModuleType,
    *,
    cell: int,
    init_container: bool,
) -> None:
    fixture = make_fixture()
    fixture.drive()
    rendered = workload_documents(fixture.world.probe_image)
    namespaces = fixture.world.applied("Namespace")
    assert [d["metadata"]["name"] for d in namespaces] == ["presentator"]
    cells = [d for d in fixture.world.applies() if d[0]["kind"] != "Namespace"]
    documents = cells[cell]
    assert {document["kind"] for document in documents} == {
        "PersistentVolumeClaim",
        "ServiceAccount",
        "StatefulSet",
    }
    spec = named(documents, "StatefulSet")["spec"]["template"]["spec"]
    (worker,) = spec["containers"]
    assert worker.pop("terminationMessagePolicy") == (
        manifests_module.TERMINATION_MESSAGE_POLICY
    )
    assert worker.pop("args", None) == (
        [manifests_module.CHECK_ARGUMENT] if init_container else None
    )
    expected = named(rendered, "StatefulSet")["spec"]["template"]["spec"]
    if not init_container:
        del expected["initContainers"]
    assert spec == expected


def container_of(documents: list[Document], kind: str) -> Document:
    """The first container of the kind's pod template, a CronJob's job template too."""
    spec = named(documents, kind)["spec"]
    return spec.get("jobTemplate", {}).get("spec", spec)["template"]["spec"][
        "containers"
    ][0]


Change = Callable[[list[Document], list[Document]], object]
CHANGES: list[tuple[Change, str]] = [
    (lambda _b, o: named(o, "Deployment")["spec"].update(replicas=3), "replicas"),
    (
        lambda _b, o: named(o, "StatefulSet")["spec"]["template"]["spec"][
            "initContainers"
        ][0].update(image="busybox:latest"),
        "initContainers",
    ),
    (
        lambda b, o: container_of(o, "Deployment").update(
            image=container_of(b, "Deployment")["image"]
        ),
        "Deployment web",
    ),
    (
        lambda b, o: container_of(o, "CronJob").update(
            image=container_of(b, "CronJob")["image"]
        ),
        "CronJob restore-test",
    ),
    (
        lambda _b, o: container_of(o, "CronJob").update(
            image=container_of(o, "CronJob")["image"] + DIGEST
        ),
        "digest",
    ),
    (
        lambda b, o: [container_of(d, "StatefulSet").update(name="x") for d in (b, o)],
        "is not at {image}",
    ),
]


@pytest.mark.parametrize(
    ("change", "named_in_refusal"),
    CHANGES,
    ids=[
        "another field changed",
        "remove-link's digest changed",
        "the web left at the production image",
        "the CronJob's container left at the production image",
        "a digest on the probe image",
        "the worker container not swapped",
    ],
)
def test_the_overlay_render_is_refused_for_each_difference_beyond_the_image_fields(
    cluster_module: ModuleType,
    manifests_module: ModuleType,
    production_image: str,
    change: Change,
    named_in_refusal: str,
) -> None:
    probe_image = f"{cluster_module.PROBE_REPOSITORY}:{RUN_IDENTIFIER}"
    base = workload_documents(production_image)
    overlay = workload_documents(probe_image)
    assert manifests_module.render_problem(base, overlay, probe_image) is None
    change(base, overlay)
    problem = manifests_module.render_problem(base, overlay, probe_image)
    assert named_in_refusal.format(image=manifests_module.PRODUCTION_IMAGE) in problem


def test_the_web_documents_are_the_overlays_config_map_and_deployment_as_rendered(
    manifests_module: ModuleType, production_image: str
) -> None:
    rendered = workload_documents(production_image)
    documents = manifests_module.web_documents(rendered)
    assert documents == [
        named(rendered, "Deployment"),
        named(rendered, "ConfigMap"),
    ]
    assert documents[0] is not named(rendered, "Deployment")


def test_the_throwaway_secret_reads_its_name_and_keys_from_the_deployment(
    manifests_module: ModuleType, production_image: str
) -> None:
    deployment = named(workload_documents(production_image), "Deployment")
    values = iter(f"effect-{number}" for number in range(10))
    secret = manifests_module.throwaway_secret(deployment, lambda: next(values))
    assert secret["kind"] == "Secret"
    assert secret["metadata"]["name"] == PROJECTED_NAME
    assert set(secret["stringData"]) == set(PROJECTED_KEYS)
    assert sorted(secret["stringData"].values()) == ["effect-0", "effect-1"]
