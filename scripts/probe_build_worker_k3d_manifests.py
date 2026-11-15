"""The k3d probe's render proof and the documents it applies.

`prove_render` renders the image-only overlay and proves it differs from
platform's own render only in the production image fields of every pod
template. `applied_documents` takes the overlay's documents the worker pods
need, `web_documents` those the web needs and `throwaway_secrets` the Secrets
the web's Deployment projects, with a value from an effect for each file they
hold. `namespace_document` takes production's Namespace for presentator.
"""

import copy
import json
import os
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, cast

from probe_build_worker_harness import refuse, say
from probe_build_worker_k3d_cluster import NAMESPACE, Document, Run, documents_of
from probe_build_worker_starts import CHECK_ARGUMENT

PRODUCTION_IMAGE = "ghcr.io/overnightworks/presentator"
APPLIED_KINDS = ("PersistentVolumeClaim", "ServiceAccount")
# What the workers are: their StatefulSet, their Service, their NetworkPolicy and
# the ConfigMap holding the packet filter, each named for the workers; the base
# renders other StatefulSets, Postgres among them, that the probe leaves out.
WORKER_KINDS = ("ConfigMap", "NetworkPolicy", "Service", "StatefulSet")
WORKERS = "build-worker"
WORKER_LABEL = "app.kubernetes.io/name"
INBOUND_PROBE_POLICY = "probe-inbound-any-port"
WEB_KINDS = ("ConfigMap", "Deployment")
WORKER_CONTAINER = "worker"
WEB_CONTAINER = "web"
SECRETS_VOLUME = "secrets"
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


def _swap_images(base: list[Document], overlay: list[Document], image: str) -> str:
    """Drop the presentator image fields of both renders; say why one is wrong."""
    worker_swapped = False
    for before, after in zip(base, overlay, strict=True):
        where = f"{after['kind']} {after['metadata']['name']}"
        for original_spec, replaced_spec in zip(
            _pod_specs(before), _pod_specs(after), strict=False
        ):
            pairs = zip(
                original_spec["containers"], replaced_spec["containers"], strict=False
            )
            for original, replaced in pairs:
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
    """The overlay's ConfigMaps and Deployment the web needs, exactly as rendered."""
    return [
        copy.deepcopy(document)
        for document in overlay
        if document["kind"] in WEB_KINDS and not _of_the_workers(document)
    ]


def _files_under(container: Document, mount: PurePosixPath) -> list[str]:
    """The last parts of the container's environment values directly under the mount."""
    files = (PurePosixPath(e["value"]) for e in container["env"] if "value" in e)
    return [file.name for file in files if file.parent == mount]


def throwaway_secrets(deployment: Document, value: Callable[[], str]) -> list[Document]:
    """The Secrets the Deployment's `secrets` volume projects, a `value()` per file.

    A source that names its keys holds those; the one that names none holds the
    last parts of the web container's environment values that lie directly under
    the volume's mount path. The workers' Secret is the one source that names its
    keys, so the web and the workers hold the one key.
    """
    pod = deployment["spec"]["template"]["spec"]
    volume = next(v for v in pod["volumes"] if v["name"] == SECRETS_VOLUME)
    container = next(c for c in pod["containers"] if c["name"] == WEB_CONTAINER)
    mounted = next(m for m in container["volumeMounts"] if m["name"] == SECRETS_VOLUME)
    under_mount = _files_under(container, PurePosixPath(mounted["mountPath"]))
    sources = [s["secret"] for s in volume["projected"]["sources"] if "secret" in s]
    return [
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": source["name"], "namespace": NAMESPACE},
            "stringData": {
                key: value()
                for key in [item["key"] for item in source.get("items", [])]
                or under_mount
            },
        }
        for source in sources
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


def namespace_document(platform: Path) -> Document:
    """Production's Namespace document for presentator, as the checkout has it."""
    path = platform / "infrastructure" / "namespaces" / "namespaces.yaml"
    return next(
        d
        for d in documents_of(path.read_text())
        if d["kind"] == "Namespace" and d["metadata"]["name"] == NAMESPACE
    )
