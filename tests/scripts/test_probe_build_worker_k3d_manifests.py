"""The k3d driver's render proof and the documents it applies."""

import configparser
import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from urllib.parse import urlsplit

import pytest

from tests.scripts.support.k3d_pods import (
    Document,
)
from tests.scripts.support.k3d_render import (
    DATABASE_CREDENTIALS,
    DATABASE_KEY_FILE,
    DATABASE_URL,
    OBJECT_STORE_BUCKET,
    OBJECT_STORE_ENDPOINT,
    OBJECT_STORE_NAME,
    OBJECT_STORE_REGION,
    PROJECTED_KEYS,
    PROJECTED_NAME,
    WORKER_KEY_NAME,
    named,
    workload_documents,
)
from tests.scripts.support.k3d_world import (
    DIGEST,
    RUN_IDENTIFIER,
    WORKER_POD_NAMES,
    Fixture,
)


@pytest.fixture
def production_image(manifests_module: ModuleType) -> str:
    return manifests_module.PRODUCTION_IMAGE + DIGEST


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
    store_namespace = str(urlsplit(OBJECT_STORE_ENDPOINT).hostname).split(".")[1]
    assert [d["metadata"]["name"] for d in namespaces] == [
        "presentator",
        store_namespace,
    ]
    cells = [
        d
        for d in fixture.world.applies()
        if d[0]["kind"] not in {"Namespace", "Secret"}
    ]
    documents = cells[cell]
    assert {(d["kind"], d["metadata"]["name"]) for d in documents} == {
        ("PersistentVolumeClaim", "builds"),
        ("ServiceAccount", "build-worker"),
        ("Service", "build-worker"),
        ("NetworkPolicy", "build-worker"),
        ("ConfigMap", "build-worker-rules-0a1b2c"),
        ("StatefulSet", "build-worker"),
    }
    assert named(documents, "StatefulSet")["spec"]["replicas"] == 1
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
        lambda b, o: named(o, "Deployment")["spec"]["template"]["spec"][
            "initContainers"
        ][0].update(
            image=named(b, "Deployment")["spec"]["template"]["spec"]["initContainers"][
                0
            ]["image"]
        ),
        "Deployment web container migrate",
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
        "nft-rules's digest changed",
        "the web left at the production image",
        "the web's init container left at the production image",
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


def test_the_web_documents_are_the_overlays_config_map_and_deployment_with_a_redis_url(
    manifests_module: ModuleType, production_image: str
) -> None:
    rendered = workload_documents(production_image)
    documents = manifests_module.web_documents(rendered)
    web = documents[0]["spec"]["template"]["spec"]["containers"][0]
    variable = manifests_module.REDIS_URL_VARIABLE
    named_redis = [e for e in web["env"] if e["name"] == variable]
    assert named_redis == [web["env"].pop()]
    assert manifests_module.REDIS in named_redis[0]["value"]
    rendered_web = container_of(rendered, "Deployment")
    rendered_web["env"] = [e for e in rendered_web["env"] if e["name"] != variable]
    assert documents == [
        named(rendered, "Deployment"),
        next(
            d
            for d in rendered
            if d["kind"] == "ConfigMap"
            and not d["metadata"]["name"].startswith("build-worker")
        ),
    ]
    assert documents[0] is not named(rendered, "Deployment")


def test_the_probe_redis_never_makes_the_cluster_pull(
    manifests_module: ModuleType,
) -> None:
    deployment = manifests_module.redis_documents("local-redis")[0]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["imagePullPolicy"] == "Never"
    assert container["image"] == "local-redis"


def test_the_throwaway_secrets_read_their_names_and_keys_from_the_deployment(
    manifests_module: ModuleType, production_image: str
) -> None:
    deployment = named(workload_documents(production_image), "Deployment")
    values = iter(f"effect-{number}" for number in range(10))
    secrets = manifests_module.throwaway_secrets(deployment, lambda: next(values))
    assert {secret["kind"] for secret in secrets} == {"Secret"}
    by_name = {secret["metadata"]["name"]: secret["stringData"] for secret in secrets}
    assert set(by_name) == {
        PROJECTED_NAME,
        WORKER_KEY_NAME,
        DATABASE_CREDENTIALS,
        OBJECT_STORE_NAME,
        manifests_module.GARAGE,
    }
    assert set(by_name[PROJECTED_NAME]) == set(PROJECTED_KEYS)
    assert set(by_name[WORKER_KEY_NAME]) == {"key"}
    assert set(by_name[DATABASE_CREDENTIALS]) == {DATABASE_KEY_FILE}
    assert set(by_name[OBJECT_STORE_NAME]) == {"credentials"}
    files = [*by_name[PROJECTED_NAME].values(), *by_name[WORKER_KEY_NAME].values()]
    files.append(by_name[DATABASE_CREDENTIALS][DATABASE_KEY_FILE])
    assert len(set(files)) == len(files)
    assert all(file.startswith("effect-") for file in files)


def test_the_web_reads_the_key_the_probe_garage_holds(
    manifests_module: ModuleType, production_image: str
) -> None:
    deployment = named(workload_documents(production_image), "Deployment")
    values = iter(f"{number:064x}" for number in range(10))
    secrets = manifests_module.throwaway_secrets(deployment, lambda: next(values))
    by_name = {secret["metadata"]["name"]: secret["stringData"] for secret in secrets}
    credentials = configparser.ConfigParser()
    credentials.read_string(by_name[OBJECT_STORE_NAME]["credentials"])
    garage = by_name[manifests_module.GARAGE]
    assert dict(credentials["default"]) == {
        "aws_access_key_id": garage["access_key"],
        "aws_secret_access_key": garage["secret_key"],
    }
    assert re.fullmatch("GK[0-9a-f]{24}", garage["access_key"])


def test_the_probe_garage_stands_under_the_endpoint_the_web_names_and_never_pulls(
    manifests_module: ModuleType, production_image: str
) -> None:
    deployment = named(workload_documents(production_image), "Deployment")
    documents = manifests_module.garage_documents(deployment, "local-garage")
    namespace, alias, configuration, garage, service = documents
    endpoint = urlsplit(OBJECT_STORE_ENDPOINT)
    host_name, host_namespace, *_ = str(endpoint.hostname).split(".")
    alias_namespace = alias["metadata"]["namespace"]
    assert (alias["metadata"]["name"], alias_namespace) == (host_name, host_namespace)
    assert namespace["metadata"]["name"] == alias_namespace
    target = service["metadata"]
    assert alias["spec"]["externalName"] == (
        f"{target['name']}.{target['namespace']}.svc.cluster.local"
    )
    assert service["spec"]["ports"][0]["port"] == endpoint.port
    assert service["spec"]["selector"] == garage["spec"]["selector"]["matchLabels"]
    (container,) = garage["spec"]["template"]["spec"]["containers"]
    assert (container["image"], container["imagePullPolicy"]) == (
        "local-garage",
        "Never",
    )
    environment = {e["name"]: e for e in container["env"]}
    assert environment["GARAGE_DEFAULT_BUCKET"]["value"] == OBJECT_STORE_BUCKET
    keys = {
        name: e["valueFrom"]["secretKeyRef"]
        for name, e in environment.items()
        if "valueFrom" in e
    }
    assert {key["name"] for key in keys.values()} == {manifests_module.GARAGE}
    held = tomllib.loads(next(iter(configuration["data"].values())))
    assert held["s3_api"]["s3_region"] == OBJECT_STORE_REGION
    assert held["s3_api"]["api_bind_addr"].endswith(f":{endpoint.port}")


def test_the_probe_postgres_is_the_one_the_web_database_url_names_and_never_pulls(
    manifests_module: ModuleType, production_image: str
) -> None:
    deployment = named(workload_documents(production_image), "Deployment")
    postgres, service = manifests_module.postgres_documents(deployment, "local-pg")
    pod = postgres["spec"]["template"]["spec"]
    (container,) = pod["containers"]
    environment = {e["name"]: e["value"] for e in container["env"]}
    (volume,) = pod["volumes"]
    (mount,) = container["volumeMounts"]
    password_file = Path(environment["POSTGRES_PASSWORD_FILE"])
    url = f"postgresql://{environment['POSTGRES_USER']}@{service['metadata']['name']}"
    url += f":{service['spec']['ports'][0]['port']}/{environment['POSTGRES_DB']}"
    assert url == DATABASE_URL
    assert service["spec"]["selector"] == postgres["spec"]["selector"]["matchLabels"]
    assert (container["image"], container["imagePullPolicy"]) == ("local-pg", "Never")
    assert volume["secret"] == {"secretName": DATABASE_CREDENTIALS}
    assert (mount["name"], Path(mount["mountPath"])) == (
        volume["name"],
        password_file.parent,
    )
    assert password_file.name == DATABASE_KEY_FILE


def test_the_workers_apply_at_the_replicas_asked_for(
    manifests_module: ModuleType, production_image: str
) -> None:
    rendered = workload_documents(production_image)
    documents = manifests_module.applied_documents(
        rendered, init_container=True, replicas=len(WORKER_POD_NAMES)
    )
    assert named(documents, "StatefulSet")["spec"]["replicas"] == len(WORKER_POD_NAMES)


def test_the_web_cells_apply_the_probe_redis_and_postgres_with_the_web(
    make_fixture: Callable[..., Fixture], manifests_module: ModuleType
) -> None:
    fixture = make_fixture()
    fixture.drive()
    (with_the_web,) = [
        documents
        for documents in fixture.world.applies()
        if ("Deployment", "web")
        in {(d["kind"], d["metadata"]["name"]) for d in documents}
    ]
    database = urlsplit(DATABASE_URL).hostname
    assert {(d["kind"], d["metadata"]["name"]) for d in with_the_web} >= {
        ("Deployment", manifests_module.REDIS),
        ("Deployment", database),
        ("Service", database),
    }
