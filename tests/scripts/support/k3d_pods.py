"""The k3d driver tests' pods: the events kubectl prints for worker lives, and nodes."""

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from presentator.host.lifecycle import ServerEvent
from presentator.host.worker import REFUSAL_EXIT_STATUS
from presentator.host.worker_self_check import Predicate

K3S_VERSION = "v1.37.2+k3s1"
OWN = "the run's own node"
EPOCH = datetime(2026, 11, 11, tzinfo=UTC)

Document = dict[str, Any]


def stamp(seconds: int) -> str:
    return (EPOCH + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def terminated_state(
    exit_code: int = REFUSAL_EXIT_STATUS,
    *,
    message: str = "",
    reason: str = "Error",
    started: int = 0,
) -> Document:
    return {
        "terminated": {
            "exitCode": exit_code,
            "reason": reason,
            "message": message,
            "startedAt": stamp(started),
            "finishedAt": stamp(started + 1),
        }
    }


@dataclass(frozen=True)
class Version:
    """What one version of a pod differs in."""

    init: bool = True
    init_exit: int | None = 0
    last_state: Document = field(default_factory=dict[str, Any])
    restarts: int = 0


PLAIN = Version()


def pod_version(
    uid: str,
    state: Document | None = None,
    version: Version = PLAIN,
) -> Document:
    """One version of a pod as kubectl's watch prints it."""
    worker: Document = {
        "name": "worker",
        "restartCount": version.restarts,
        "state": state or {"waiting": {}},
        "lastState": version.last_state,
    }
    status: Document = {"containerStatuses": [worker]}
    spec: Document = {"containers": [{"name": "worker"}]}
    if version.init:
        spec["initContainers"] = [{"name": "nft-rules"}]
        init_state: Document = {"running": {}}
        if version.init_exit is not None:
            init_state = terminated_state(version.init_exit)
        status["initContainerStatuses"] = [{"name": "nft-rules", "state": init_state}]
    return {"metadata": {"uid": uid}, "spec": spec, "status": status}


def watch_event(kind: str, pod: Document) -> Document:
    assert kind in {"ADDED", "MODIFIED", "DELETED"}, f"the fake does not know {kind}"
    return {"type": kind, "object": pod}


def ending(refusal: Predicate | None, *, started: int = 0) -> Document:
    """The terminated state of a worker that refuses or, with none, exits 0."""
    if refusal is None:
        return terminated_state(0, reason="Completed", started=started)
    said = (
        '{"event": "process.started", "service": "presentator-worker"}\n'
        + json.dumps({"event": ServerEvent.PROCESS_FAILED, "reason": refusal})
        + "\n"
    )
    return terminated_state(message=said, started=started)


def life_events(
    uid: str,
    refusal: Predicate | None,
    *,
    started: int = 0,
    ended: Document | None = None,
    version: Version = PLAIN,
) -> list[Document]:
    """The events of one life that ends by a refusal or, with none, by exit 0."""
    state = ended or ending(refusal, started=started)
    pending = pod_version(uid, version=replace(version, init_exit=None))
    last = pod_version(uid, state, version)
    return [
        watch_event("ADDED", pending),
        watch_event("MODIFIED", last),
        watch_event("DELETED", last),
    ]


def lives_stream(
    count: int,
    refusal: Predicate | None,
    *,
    gap: int = 3,
    version: Version = PLAIN,
) -> list[Document]:
    """`count` lives, each worker starting `gap` seconds after its predecessor's end."""
    return [
        event
        for number in range(count)
        for event in life_events(
            f"life-{number}", refusal, started=number * (gap + 1), version=version
        )
    ]


def twin_answer(
    refusal: Predicate | None, state: Document | None = None, version: Version = PLAIN
) -> Document:
    """The bare Pod kubectl prints once its worker ended as `state` or as expected."""
    ended = state or ending(refusal)
    pod = pod_version("twin-uid", ended, version)
    phase = "Succeeded" if ended["terminated"]["exitCode"] == 0 else "Failed"
    pod["status"]["phase"] = phase
    return pod


def node(
    name: str = OWN, *, ready: bool = True, version: str = K3S_VERSION
) -> Document:
    return {
        "metadata": {"name": name},
        "status": {
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
            "nodeInfo": {"kubeletVersion": version},
        },
    }
