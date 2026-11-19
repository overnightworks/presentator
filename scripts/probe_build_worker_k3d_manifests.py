"""The k3d probe's render proof and the documents it applies.

`prove_render` renders the image-only overlay and proves it differs from
platform's own render only in the production image fields of every pod
template. `applied_documents` takes the overlay's documents the worker pods
need, `web_documents` those the web needs, `redis_documents`,
`postgres_documents` and `garage_documents` the throwaway stores it reaches, and
`throwaway_secrets` the Secrets the web's Deployment and the probe's Garage
read, with a value from an effect for each file they hold. `namespace_document`
takes production's Namespace for presentator.
"""

import copy
import json
import os
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, cast
from urllib.parse import urlsplit

from probe_build_worker_harness import refuse, say
from probe_build_worker_k3d_cluster import NAMESPACE, Document, Run, documents_of
from probe_build_worker_starts import CHECK_ARGUMENT

PRODUCTION_IMAGE = "ghcr.io/overnightworks/presentator"
APPLIED_KINDS = ("PersistentVolumeClaim", "ServiceAccount")
# What the workers are: their StatefulSet, their Service, their NetworkPolicy and
# the ConfigMap holding the packet filter, each named for the workers; the base
# renders other StatefulSets, its Postgres and Redis among them, that the probe
# leaves out for throwaway ones the cluster never pulls.
WORKER_KINDS = ("ConfigMap", "NetworkPolicy", "Service", "StatefulSet")
WORKERS = "build-worker"
WORKER_LABEL = "app.kubernetes.io/name"
INBOUND_PROBE_POLICY = "probe-inbound-any-port"
INBOUND_CANARY_POLICY = "probe-inbound-canary"
WEB_KINDS = ("ConfigMap", "Deployment")
WORKER_CONTAINER = "worker"
WEB_CONTAINER = "web"
# The web needs a Redis and a Postgres; the probe runs a throwaway one of each in
# its namespace, from the image that CI's service container of that store uses.
# The runner pulls each pinned digest from a mirror without Docker Hub's
# anonymous rate limit and imports it under a local name; the cluster never
# pulls it.
REDIS = "probe-redis"
REDIS_PINNED = (
    "mirror.gcr.io/library/redis:7.4.12-alpine"
    "@sha256:338fbfc036dc5517ac0b04d6de21521085935336e565f223406180e9d84166e2"
)
REDIS_PORT = 6379
REDIS_URL_VARIABLE = "PRESENTATOR_REDIS_URL"
POSTGRES_PINNED = (
    "mirror.gcr.io/library/postgres:18.6-alpine3.24"
    "@sha256:123b17998ccaf47e53b502e8c6a1e79afd8f579c6f3eec83c563da026f957395"
)
DATABASE_URL_VARIABLE = "PRESENTATOR_DATABASE_URL"
DATABASE_PASSWORD_FILE_VARIABLE = "PRESENTATOR_DATABASE_PASSWORD_FILE"  # noqa: S105 - the value names an environment variable and is not a password
# Where the probe's Postgres reads the password the web reads from its own mount.
POSTGRES_PASSWORD_MOUNT = PurePosixPath("/run/secrets/postgres")
# The web reaches the object store at the endpoint its environment names. The
# probe runs a throwaway Garage in its namespace, at the image platform's object
# store and CI's Garage run, pulled from Docker Hub as CI's is, and names it
# under the endpoint's own host with an ExternalName Service, so the web's store
# settings stay as platform renders them. Garage makes the web's bucket and its
# one key from its own environment, so no command carries the key.
GARAGE = "probe-garage"
GARAGE_PINNED = (
    "docker.io/dxflrs/garage:v2.5.0"
    "@sha256:677e5b758e4425a325b811c425c026bf35a4bf42f7ddbaa773de3f4fd779a5a5"
)
GARAGE_CONFIGURATION = PurePosixPath("/etc/garage/garage.toml")
ENDPOINT_VARIABLE = "AWS_ENDPOINT_URL_S3"
REGION_VARIABLE = "AWS_DEFAULT_REGION"
CREDENTIALS_FILE_VARIABLE = "AWS_SHARED_CREDENTIALS_FILE"
BUCKET_VARIABLE = "PRESENTATOR_S3_BUCKET"
# Garage takes a key ID of GK and 24 hex digits.
KEY_ID_DIGITS = 24
INIT_CONTAINER = "nft-rules"
TERMINATION_MESSAGE_POLICY = "FallbackToLogsOnError"


def _first_difference(before: Any, after: Any, path: str) -> str | None:  # noqa: ANN401 - a parsed YAML value is any scalar, list or mapping
    pairs: list[tuple[Any, Any, str]] = []
    if isinstance(before, dict) and isinstance(after, dict):
        left, right = cast("Document", before), cast("Document", after)
        for key in sorted(left.keys() | right.keys()):
            if key not in left or key not in right:
                return f"{path}.{key}: present on one side only"
            pairs.append((left[key], right[key], f"{path}.{key}"))
    elif isinstance(before, list) and isinstance(after, list):
        before, after = cast("list[Any]", before), cast("list[Any]", after)
        if len(before) != len(after):
            return f"{path}: {len(before)} items against {len(after)}"
        pairs = [
            (b, a, f"{path}[{i}]")
            for i, (b, a) in enumerate(zip(before, after, strict=True))
        ]
    elif before != after:
        return f"{path}: {before!r} against {after!r}"
    return next(filter(None, (_first_difference(*pair) for pair in pairs)), None)


def _pod_specs(document: Document) -> list[Document]:
    """The pod specs of a workload: its template's and a CronJob's job template's."""
    spec = document.get("spec", {})
    templates = [
        spec.get("template"),
        spec.get("jobTemplate", {}).get("spec", {}).get("template"),
    ]
    return [template["spec"] for template in templates if template]


def _wrong_image(image: str) -> str:
    if image.startswith(PRODUCTION_IMAGE):
        return f"is left at {image}"
    return "carries a digest" if "@" in image else f"names {image}"


def _container_pairs(
    before: Document, after: Document
) -> list[tuple[Document, Document]]:
    """The init containers and containers of two pod specs, side by side."""
    return [
        pair
        for field in ("initContainers", "containers")
        for pair in zip(before.get(field, []), after.get(field, []), strict=False)
    ]


def _swap_images(base: list[Document], overlay: list[Document], image: str) -> str:
    """Drop the presentator image fields of both renders; say why one is wrong."""
    worker_swapped = False
    for before, after in zip(base, overlay, strict=True):
        where = f"{after['kind']} {after['metadata']['name']}"
        for original_spec, replaced_spec in zip(
            _pod_specs(before), _pod_specs(after), strict=False
        ):
            for original, replaced in _container_pairs(original_spec, replaced_spec):
                if not original.get("image", "").startswith(PRODUCTION_IMAGE):
                    continue
                if replaced["image"] != image:
                    reason = _wrong_image(replaced["image"])
                    return f"{where} container {replaced['name']} {reason}, not {image}"
                del original["image"], replaced["image"]
                worker_swapped = worker_swapped or (
                    after["kind"] == "StatefulSet"
                    and original["name"] == WORKER_CONTAINER
                )
    if not worker_swapped:
        return f"the StatefulSet's {WORKER_CONTAINER} is not at {PRODUCTION_IMAGE}"
    return ""


def render_problem(
    base: list[Document], overlay: list[Document], image: str
) -> str | None:
    """Why the overlay differs from the base beyond the production image fields."""
    base, overlay = copy.deepcopy(base), copy.deepcopy(overlay)
    if len(base) != len(overlay):
        return f"the overlay renders {len(overlay)} documents, the base {len(base)}"
    if problem := _swap_images(base, overlay, image):
        return problem
    for before, after in zip(base, overlay, strict=True):
        label = f"{after['kind']} {after['metadata']['name']}"
        if found := _first_difference(before, after, label):
            return found
    return None


def prove_render(run: Run) -> list[Document]:
    """Render the image-only overlay, prove it against the base and give its render."""
    app = str(run.platform / "apps" / "presentator")
    overlay = run.directory / "overlay"
    overlay.mkdir()
    repository, _, tag = run.tag.rpartition(":")
    swap = {"name": PRODUCTION_IMAGE, "newName": repository, "newTag": tag}
    (overlay / "kustomization.yaml").write_text(
        json.dumps({"resources": [os.path.relpath(app, overlay)], "images": [swap]})
    )
    base = documents_of(run("kubectl kustomize", app).stdout)
    rendered = documents_of(run("kubectl kustomize", str(overlay)).stdout)
    if problem := render_problem(base, rendered, run.tag):
        refuse(f"the overlay render: {problem}")
    say("cell 5: the overlay changes only the production image fields")
    return rendered


def _of_the_workers(document: Document) -> bool:
    return document["metadata"]["name"].startswith(WORKERS)


def applied_documents(
    overlay: list[Document],
    *,
    init_container: bool,
    check: bool = False,
    replicas: int = 1,
) -> list[Document]:
    """The overlay's documents the worker pods need.

    The StatefulSet runs `replicas` pods; the worker's message policy is set, and
    its arguments are `--check` when `check`.
    """
    documents = [
        copy.deepcopy(document)
        for document in overlay
        if document["kind"] in APPLIED_KINDS
        or (document["kind"] in WORKER_KINDS and _of_the_workers(document))
    ]
    for document in documents:
        if document["kind"] != "StatefulSet":
            continue
        document["spec"]["replicas"] = replicas
        spec = document["spec"]["template"]["spec"]
        if not init_container:
            del spec["initContainers"]
        for container in spec["containers"]:
            if container["name"] == WORKER_CONTAINER:
                container["terminationMessagePolicy"] = TERMINATION_MESSAGE_POLICY
                if check:
                    container["args"] = [CHECK_ARGUMENT]
    return documents


def web_documents(overlay: list[Document]) -> list[Document]:
    """The overlay's ConfigMaps and Deployment the web needs, as rendered.

    The web container's Redis URL names the probe's Redis instead of the
    render's; no worker pod gets it.
    """
    documents = [
        copy.deepcopy(document)
        for document in overlay
        if document["kind"] in WEB_KINDS and not _of_the_workers(document)
    ]
    for document in documents:
        if document["kind"] != "Deployment":
            continue
        for container in document["spec"]["template"]["spec"]["containers"]:
            if container["name"] == WEB_CONTAINER:
                url = f"redis://{REDIS}.{NAMESPACE}.svc.cluster.local:{REDIS_PORT}/0"
                container["env"] = [
                    *(e for e in container["env"] if e["name"] != REDIS_URL_VARIABLE),
                    {"name": REDIS_URL_VARIABLE, "value": url},
                ]
    return documents


def redis_documents(image: str) -> list[Document]:
    """The Deployment and Service of the probe's throwaway Redis, from `image`."""
    labels = {"app.kubernetes.io/name": REDIS}
    metadata = {"name": REDIS, "namespace": NAMESPACE, "labels": labels}
    return [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": metadata,
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "containers": [
                            {
                                "name": "redis",
                                "image": image,
                                "imagePullPolicy": "Never",
                                "ports": [{"containerPort": REDIS_PORT}],
                            }
                        ]
                    },
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": metadata,
            "spec": {
                "selector": labels,
                "ports": [{"port": REDIS_PORT, "targetPort": REDIS_PORT}],
            },
        },
    ]


def _environment_value(container: Document, name: str) -> str:
    return next(e["value"] for e in container["env"] if e["name"] == name)


def _web_pod(deployment: Document) -> tuple[Document, Document]:
    """The Deployment's pod spec and its web container."""
    pod = deployment["spec"]["template"]["spec"]
    return pod, next(c for c in pod["containers"] if c["name"] == WEB_CONTAINER)


def postgres_documents(deployment: Document, image: str) -> list[Document]:
    """The Deployment and Service of the probe's throwaway Postgres, from `image`.

    The web's database URL names the Service, its port, the user and the
    database; the Postgres reads its password from the Secret key the web reads
    as its password file, so both hold the one throwaway value.
    """
    pod, container = _web_pod(deployment)
    url = urlsplit(_environment_value(container, DATABASE_URL_VARIABLE))
    password = PurePosixPath(
        _environment_value(container, DATABASE_PASSWORD_FILE_VARIABLE)
    )
    mount = next(
        m for m in container["volumeMounts"] if m["mountPath"] == str(password.parent)
    )
    volume = next(v for v in pod["volumes"] if v["name"] == mount["name"])
    name, port = url.hostname, url.port
    labels = {"app.kubernetes.io/name": name}
    metadata = {"name": name, "namespace": NAMESPACE, "labels": labels}
    environment = {
        "POSTGRES_USER": url.username,
        "POSTGRES_DB": url.path.removeprefix("/"),
        "POSTGRES_PASSWORD_FILE": str(POSTGRES_PASSWORD_MOUNT / password.name),
    }
    secret = {"name": "password", "secret": volume["secret"]}
    postgres = {
        "name": "postgres",
        "image": image,
        "imagePullPolicy": "Never",
        "env": [{"name": key, "value": value} for key, value in environment.items()],
        "ports": [{"containerPort": port}],
        "readinessProbe": {"exec": {"command": ["pg_isready", "-U", url.username]}},
        "volumeMounts": [
            {"name": "password", "mountPath": str(POSTGRES_PASSWORD_MOUNT)}
        ],
    }
    return [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": metadata,
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {"containers": [postgres], "volumes": [secret]},
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": metadata,
            "spec": {"selector": labels, "ports": [{"port": port, "targetPort": port}]},
        },
    ]


def _files_under(container: Document, mount: PurePosixPath) -> list[str]:
    """The last parts of the container's environment values directly under the mount."""
    files = (PurePosixPath(e["value"]) for e in container["env"] if "value" in e)
    return [file.name for file in files if file.parent == mount]


def _secret_sources(volume: Document) -> list[Document]:
    """The Secrets a volume holds, each by its name and the keys it names, if any."""
    if "secret" in volume:
        secret = volume["secret"]
        return [{"name": secret["secretName"], "items": secret.get("items", [])}]
    sources = volume.get("projected", {}).get("sources", [])
    return [source["secret"] for source in sources if "secret" in source]


def _keys_and_files(
    source: Document, container: Document, mount: PurePosixPath
) -> list[tuple[str, PurePosixPath]]:
    """Each key of the Secret and the file the web container reads it as."""
    items = [(item["key"], item["path"]) for item in source.get("items", [])]
    named = items or [(name, name) for name in _files_under(container, mount)]
    return [(key, mount / path) for key, path in named]


def _secret(name: str, held: dict[str, str]) -> Document:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": NAMESPACE},
        "stringData": held,
    }


def throwaway_secrets(deployment: Document, value: Callable[[], str]) -> list[Document]:
    """The Secrets the web container mounts and the probe's Garage reads.

    Each file the web reads holds a `value()`, but one. A Secret that names its
    keys holds those; one that names none holds the last parts of the web
    container's environment values that lie directly under the mount path of
    its volume. The workers' Secret names its keys, so the web and the workers
    hold the one key, and the database password's Secret names none, so the web
    and the probe's Postgres hold the one password. The one file is the web's
    object store credentials file: it holds the key that the Secret GARAGE gives
    the probe's Garage.
    """
    pod, container = _web_pod(deployment)
    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    credentials = PurePosixPath(
        _environment_value(container, CREDENTIALS_FILE_VARIABLE)
    )
    key_id, secret = f"GK{value()[:KEY_ID_DIGITS]}", value()
    credentials_file = (
        f"[default]\naws_access_key_id = {key_id}\naws_secret_access_key = {secret}\n"
    )
    web = [
        _secret(
            source["name"],
            {
                key: credentials_file if file == credentials else value()
                for key, file in _keys_and_files(
                    source, container, PurePosixPath(mount["mountPath"])
                )
            },
        )
        for mount in container["volumeMounts"]
        for source in _secret_sources(volumes[mount["name"]])
    ]
    garage = {"rpc_secret": value(), "access_key": key_id, "secret_key": secret}
    return [*web, _secret(GARAGE, garage)]


def _garage_configuration(region: str, port: int) -> str:
    """One node that talks to itself only, its S3 API at `port` in `region`."""
    return "\n".join(
        (
            "replication_factor = 1",
            'db_engine = "sqlite"',
            'metadata_dir = "/data/meta"',
            'data_dir = "/data/data"',
            'rpc_bind_addr = "127.0.0.1:3901"',
            'rpc_public_addr = "127.0.0.1:3901"',
            "[s3_api]",
            f's3_region = "{region}"',
            f'api_bind_addr = "0.0.0.0:{port}"',
            "",
        )
    )


def _garage_secret(variable: str, key: str) -> Document:
    return {
        "name": variable,
        "valueFrom": {"secretKeyRef": {"name": GARAGE, "key": key}},
    }


def _garage_container(web: Document, image: str, port: int) -> Document:
    """Garage from `image`, its S3 API at `port`, with the web's bucket and key."""
    return {
        "name": "garage",
        "image": image,
        "imagePullPolicy": "Never",
        "command": ["/garage"],
        "args": ["server", "--single-node", "--default-bucket"],
        "env": [
            {"name": "GARAGE_CONFIG_FILE", "value": str(GARAGE_CONFIGURATION)},
            {
                "name": "GARAGE_DEFAULT_BUCKET",
                "value": _environment_value(web, BUCKET_VARIABLE),
            },
            _garage_secret("GARAGE_RPC_SECRET", "rpc_secret"),
            _garage_secret("GARAGE_DEFAULT_ACCESS_KEY", "access_key"),
            _garage_secret("GARAGE_DEFAULT_SECRET_KEY", "secret_key"),
        ],
        "ports": [{"containerPort": port}],
        "readinessProbe": {"tcpSocket": {"port": port}},
        "volumeMounts": [
            {"name": "configuration", "mountPath": str(GARAGE_CONFIGURATION.parent)},
            {"name": "data", "mountPath": "/data"},
        ],
    }


def _endpoint_alias(service: str, namespace: str) -> list[Document]:
    """That namespace and its Service, an ExternalName of the Garage's own."""
    return [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}},
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": service, "namespace": namespace},
            "spec": {
                "type": "ExternalName",
                "externalName": f"{GARAGE}.{NAMESPACE}.svc.cluster.local",
            },
        },
    ]


def garage_documents(deployment: Document, image: str) -> list[Document]:
    """The probe's throwaway Garage, from `image`, under the endpoint the web names.

    The endpoint's host is a Service of another namespace; the probe makes that
    Namespace and that Service, an ExternalName of the Garage's own Service.
    Garage lays out its one node and makes the web's bucket with the key of the
    Secret GARAGE, the key the web's credentials file holds.
    """
    _, web = _web_pod(deployment)
    endpoint = urlsplit(_environment_value(web, ENDPOINT_VARIABLE))
    service, namespace, *_ = str(endpoint.hostname).split(".")
    port = endpoint.port or 80
    labels = {"app.kubernetes.io/name": GARAGE}
    metadata = {"name": GARAGE, "namespace": NAMESPACE, "labels": labels}
    configuration = _garage_configuration(
        _environment_value(web, REGION_VARIABLE), port
    )
    volumes: list[Document] = [
        {"name": "configuration", "configMap": {"name": GARAGE}},
        {"name": "data", "emptyDir": {}},
    ]
    pod: Document = {
        "containers": [_garage_container(web, image, port)],
        "volumes": volumes,
    }
    return [
        *_endpoint_alias(service, namespace),
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": metadata,
            "data": {GARAGE_CONFIGURATION.name: configuration},
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": metadata,
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": labels},
                "template": {"metadata": {"labels": labels}, "spec": pod},
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": metadata,
            "spec": {"selector": labels, "ports": [{"port": port, "targetPort": port}]},
        },
    ]


def inbound_probe_policy(deployment: Document) -> Document:
    """A NetworkPolicy admitting the web's pods to every TCP port of the workers' pods.

    Policies add up, so with this one applied beside the workers' own, which
    admits the build port alone, the pod's packet filter is all that can drop a
    web pod's connection to another port of a worker pod.
    """
    web_labels = deployment["spec"]["selector"]["matchLabels"]
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": INBOUND_PROBE_POLICY, "namespace": NAMESPACE},
        "spec": {
            "podSelector": {"matchLabels": {WORKER_LABEL: WORKERS}},
            "policyTypes": ["Ingress"],
            "ingress": [{"from": [{"podSelector": {"matchLabels": web_labels}}]}],
        },
    }


def inbound_canary_policy(deployment: Document) -> Document:
    """A NetworkPolicy admitting nothing to the web's pods.

    The web's pods have no policy of their own in the probe, so one pod reaches
    another until the cluster enforces this one: the refusal that follows shows
    the policies applied beside it are in force.
    """
    web_labels = deployment["spec"]["selector"]["matchLabels"]
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": INBOUND_CANARY_POLICY, "namespace": NAMESPACE},
        "spec": {
            "podSelector": {"matchLabels": web_labels},
            "policyTypes": ["Ingress"],
        },
    }


def namespace_document(platform: Path) -> Document:
    """Production's Namespace document for presentator, as the checkout has it."""
    path = platform / "infrastructure" / "namespaces" / "namespaces.yaml"
    return next(
        d
        for d in documents_of(path.read_text())
        if d["kind"] == "Namespace" and d["metadata"]["name"] == NAMESPACE
    )
