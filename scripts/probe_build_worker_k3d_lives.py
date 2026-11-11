"""The k3d probe's watch of the worker pod and its judgement of each life.

A cell's worker pods are replaced by platform, so a cell judges its lives from
one watch. `read_lives` folds the watch's events into `Life` records, the
judgements name the first life or interval that is not what the cell expects,
and `report_lives` prints each life's uid and refusal line.
"""

import contextlib
import itertools
import json
import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Self

from probe_build_worker_harness import say
from probe_build_worker_k3d_cluster import IN_NAMESPACE, Document, Run
from probe_build_worker_k3d_manifests import INIT_CONTAINER, WORKER_CONTAINER

from presentator.adapters.build_worker import CONNECT_WINDOW_SECONDS
from presentator.host.worker import REFUSAL_EXIT_STATUS
from presentator.host.worker_self_check import Predicate

WORKER_POD = "build-worker-0"
# What the kubelet reports for a container whose sandbox was lost.
LOST_SANDBOX_REASON = "ContainerStatusUnknown"
TERMINATION_WINDOW_SECONDS = 180
LIVES = 4
TIMESTAMP_RESOLUTION_SECONDS = 1


@dataclass(frozen=True)
class Expectation:
    """What a cell's worker pods must show."""

    refusal: Predicate
    init_container: bool
    lives: int


@dataclass(frozen=True)
class Termination:
    """One end of the worker container, as the kubelet reported it."""

    exit_code: int
    reason: str
    message: str
    started: str
    finished: str

    @classmethod
    def of(cls, state: Document) -> Self:
        """The termination a `terminated` state describes."""
        return cls(
            exit_code=state["exitCode"],
            reason=state.get("reason", ""),
            message=state.get("message", ""),
            started=state.get("startedAt", ""),
            finished=state.get("finishedAt", ""),
        )


@dataclass(frozen=True)
class Life:
    """What every version of one pod uid showed."""

    uid: str
    has_init: bool = False
    init_exit: int | None = None
    terminations: frozenset[Termination] = frozenset()
    restarts: int = 0
    deleted: bool = False

    def after(self, pod: Document, *, deleted: bool) -> Self:
        """This life with one more version of its pod."""
        status = pod.get("status", {})
        worker = _status_of(status.get("containerStatuses", []), WORKER_CONTAINER)
        init = _status_of(status.get("initContainerStatuses", []), INIT_CONTAINER)
        init_ended = init.get("state", {}).get("terminated")
        ended = (
            worker.get(key, {}).get("terminated") for key in ("state", "lastState")
        )
        return type(self)(
            uid=self.uid,
            has_init=self.has_init
            or bool(pod.get("spec", {}).get("initContainers"))
            or "initContainerStatuses" in status,
            init_exit=init_ended["exitCode"] if init_ended else self.init_exit,
            terminations=self.terminations | {Termination.of(t) for t in ended if t},
            restarts=max(self.restarts, worker.get("restartCount", 0)),
            deleted=self.deleted or deleted,
        )


def _status_of(statuses: list[Document], name: str) -> Document:
    return next((s for s in statuses if s["name"] == name), Document())


def watch_events(lines: Iterable[str]) -> Iterator[Document]:
    """The watch events of kubectl's output, each as soon as its last line arrives.

    kubectl prints the events as concatenated JSON objects, so no line ends one.
    A watch that ends inside an event yields only the events before it, so the
    cell judges the lives read.
    """
    decoder = json.JSONDecoder()
    buffer = ""
    for line in lines:
        buffer = (buffer + line).lstrip()
        while buffer:
            try:
                event, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                break
            yield event
            buffer = buffer[end:].lstrip()


def read_lives(events: Iterable[Document], count: int) -> list[Life]:
    """Fold the events into lives; stop at `count` ended lives or an ERROR event."""
    lives: dict[str, Life] = {}
    for event in events:
        if event["type"] == "ERROR":
            break
        pod = event["object"]
        uid = pod["metadata"]["uid"]
        known = lives.get(uid, Life(uid))
        lives[uid] = known.after(pod, deleted=event["type"] == "DELETED")
        if sum(life.deleted for life in lives.values()) == count:
            break
    return list(lives.values())


def watch_lives(run: Run, expected: Expectation) -> list[Life]:
    """Read the worker pod's lives from one watch that `timeout` bounds."""
    later = (expected.lives - 1) * CONNECT_WINDOW_SECONDS
    budget = math.ceil(TERMINATION_WINDOW_SECONDS + later)
    # A field selector, not the pod's name: the pod may not exist yet.
    lines = run.watch(
        f"timeout {budget} kubectl get pods --field-selector metadata.name={WORKER_POD}"
        " --watch --output-watch-events --output json",
        *IN_NAMESPACE,
    )
    with contextlib.closing(lines):
        return read_lives(watch_events(lines), expected.lives)


def refusal_line(message: str, refusal: Predicate) -> str | None:
    """The message's line that names the refusal, if one does."""
    needle = f"start refused: {refusal}"
    return next((line.strip() for line in message.splitlines() if needle in line), None)


def _worker_reason(life: Life, expected: Expectation) -> str | None:
    if len(life.terminations) != 1:
        count = len(life.terminations)
        return (
            f"the worker terminated {count} times"
            if count
            else "the Pod was deleted before its worker terminated"
        )
    (ended,) = life.terminations
    wrong_status = f"the worker ended with exit status {ended.exit_code}"
    no_line = (
        f"no 'start refused: {expected.refusal}' in {ended.message.strip()[-500:]}"
    )
    reasons = (
        (ended.reason == LOST_SANDBOX_REASON, f"the worker ended as {ended.reason}"),
        (bool(life.restarts), f"restartCount is {life.restarts}, expected 0"),
        (ended.exit_code != REFUSAL_EXIT_STATUS, wrong_status),
        (refusal_line(ended.message, expected.refusal) is None, no_line),
    )
    return next((reason for failed, reason in reasons if failed), None)


def life_problem(life: Life, expected: Expectation) -> str | None:
    """Why one life is not what the cell expects; names its uid."""
    shown = "shows" if life.has_init else "lacks"
    if life.has_init != expected.init_container:
        reason = f"the Pod {shown} an init container"
    elif expected.init_container and life.init_exit != 0:
        reason = f"{INIT_CONTAINER} ended as {life.init_exit}, expected exit status 0"
    else:
        reason = _worker_reason(life, expected)
    return f"uid {life.uid}: {reason}" if reason else None


def lives_problem(lives: list[Life], expected: Expectation) -> str | None:
    """Why the lives read are not the cell's expected ones."""
    if len(lives) != expected.lives or not all(life.deleted for life in lives):
        seen = ", ".join(f"{life.uid} (deleted: {life.deleted})" for life in lives)
        return f"the watch ended after {len(lives)} of {expected.lives} lives: {seen}"
    return next(filter(None, (life_problem(life, expected) for life in lives)), None)


def _worker_end(life: Life) -> Termination:
    (termination,) = life.terminations
    return termination


def interval_problem(number: int, lives: list[Life]) -> str | None:
    """Print each interval from a life's worker end to its successor's start.

    Name the first successor that started at or after the web's retry window.
    """
    ends = [_worker_end(life) for life in lives]
    problem = None
    for position, (before, after) in enumerate(itertools.pairwise(ends), start=1):
        started, finished = after.started, before.finished
        gap = (
            datetime.fromisoformat(started) - datetime.fromisoformat(finished)
        ).total_seconds()
        say(
            f"cell {number}: interval {position}: {gap:g} s, resolution "
            f"{TIMESTAMP_RESOLUTION_SECONDS} s, limit {CONNECT_WINDOW_SECONDS:g} s"
        )
        if gap >= CONNECT_WINDOW_SECONDS and problem is None:
            uid = lives[position].uid
            problem = f"uid {uid}: its worker started {gap:g} s after life {position}"
    return problem


def report_lives(number: int, lives: list[Life], refusal: Predicate) -> None:
    """Print every life's uid and refusal line."""
    for position, life in enumerate(lives, start=1):
        line = refusal_line(_worker_end(life).message, refusal)
        who = f"cell {number}: life {position}, uid {life.uid}"
        say(f"{who}, exit {REFUSAL_EXIT_STATUS}, {line}")
