"""The k3d driver tests' fake platform render: the workloads it holds."""

import json
from typing import Any

from tests.scripts.support.k3d_pods import Document

NFT_IMAGE = "docker.io/library/busybox:1.38.0@sha256:" + "b" * 64
SECRETS_VOLUME = "secrets"
PROJECTED_NAME = "web-secrets"
PROJECTED_MOUNT = "/run/fake-secrets"
PROJECTED_KEYS = ("first_key", "second_key")
WEB_LABELS = {"app": "fake-web", "tier": "front"}
WORKER_KEY_NAME = "fake-worker-key"
WORKER_KEY_FILE = f"{PROJECTED_MOUNT}/fake-key-directory/key"
DATABASE_CREDENTIALS = "fake-database"
DATABASE_MOUNT = "/run/fake-database"
DATABASE_KEY_FILE = "fake-password"
DATABASE_URL = "postgresql://fake-user@fake-postgres:6543/fake-database"
SCRATCH_MOUNT = "/data/elsewhere"
WEB_PORT = 8000
WORKER_PORT = 7100


def add_web_secrets(spec: Document) -> None:
    """Give the web container's pod spec a projected Secret and the files naming it."""
    (main,) = spec["containers"]
    main["env"] = [
        {"name": "AN_ORIGIN", "value": "https://example.org"},
        {"name": "A_SECRET_FILE", "value": f"{PROJECTED_MOUNT}/{PROJECTED_KEYS[0]}"},
        {"name": "AN_OTHER_PATH", "value": SCRATCH_MOUNT},
        {"name": "A_REFERENCE", "valueFrom": {"fieldRef": {}}},
        {"name": "ANOTHER_FILE", "value": f"{PROJECTED_MOUNT}/{PROJECTED_KEYS[1]}"},
        {"name": "A_WORKER_KEY_FILE", "value": WORKER_KEY_FILE},
        {"name": "PRESENTATOR_REDIS_URL", "value": "redis://render-redis:6379/0"},
        {"name": "PRESENTATOR_DATABASE_URL", "value": DATABASE_URL},
        {
            "name": "PRESENTATOR_DATABASE_PASSWORD_FILE",
            "value": f"{DATABASE_MOUNT}/{DATABASE_KEY_FILE}",
        },
    ]
    main["volumeMounts"] = [
        {"name": "elsewhere", "mountPath": SCRATCH_MOUNT},
        {"name": SECRETS_VOLUME, "mountPath": PROJECTED_MOUNT},
        {"name": "database-password", "mountPath": DATABASE_MOUNT},
    ]
    keyed = {
        "name": WORKER_KEY_NAME,
        "items": [{"key": "key", "path": "fake-key-directory/key"}],
    }
    projected = {"sources": [{"secret": {"name": PROJECTED_NAME}}, {"secret": keyed}]}
    spec["volumes"] = [
        {"name": "elsewhere", "emptyDir": {}},
        {"name": SECRETS_VOLUME, "projected": projected},
        {"name": "database-password", "secret": {"secretName": DATABASE_CREDENTIALS}},
    ]


def workload_documents(image: str) -> list[Document]:
    """What the overlay and the base render, every production container at `image`."""

    def template(container: str, *, init: bool = False) -> Document:
        main: Document = {"name": container, "image": image}
        if container == "worker":
            main["command"] = ["serve-the-build-worker", "--flag"]
            main["ports"] = [{"name": "build", "containerPort": WORKER_PORT}]
        if container == "web":
            main["ports"] = [{"name": "http", "containerPort": WEB_PORT}]
        spec: Document = {"containers": [main]}
        if container == "web":
            add_web_secrets(spec)
            spec["initContainers"] = [{"name": "migrate", "image": image}]
        if init:
            spec["initContainers"] = [{"name": "nft-rules", "image": NFT_IMAGE}]
        return {"spec": spec}

    def document(kind: str, name: str, **spec: Any) -> Document:  # noqa: ANN401
        return {"kind": kind, "metadata": {"name": name}, "spec": spec}

    job = {"spec": {"template": template("restore-test")}}
    return [
        document(
            "Deployment",
            "web",
            replicas=1,
            selector={"matchLabels": WEB_LABELS},
            template=template("web"),
        ),
        document(
            "StatefulSet",
            "build-worker",
            replicas=2,
            template=template("worker", init=True),
        ),
        document("StatefulSet", "postgres", replicas=1, template=template("postgres")),
        document("CronJob", "restore-test", jobTemplate=job),
        document("PersistentVolumeClaim", "builds"),
        document("ServiceAccount", "build-worker"),
        document("Service", "build-worker"),
        document("NetworkPolicy", "build-worker"),
        document("NetworkPolicy", "http-from-traefik-only"),
        document("ConfigMap", "build-worker-rules-0a1b2c"),
        document("ConfigMap", "web-settings"),
        document("Schedule", "backup"),
        document(
            "PodConfig",
            "backup",
            template={"spec": {"containers": [{"name": "backup"}]}},
        ),
    ]


def named(documents: list[Document], kind: str) -> Document:
    return next(document for document in documents if document["kind"] == kind)


def as_yaml_stream(documents: list[Document]) -> str:
    return "\n---\n".join(json.dumps(document) for document in documents)
