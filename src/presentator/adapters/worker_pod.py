"""The build worker's pod as the Kubernetes API shows it, and its replacement.

Each life of the worker ends with its container's exit, so a pod that has
ended one cannot serve another: the API's status is the only record of that,
and the web keeps none. A used pod is ended by the uid read from the API, so a
retried deletion can only hit the generation that was judged, never a successor.
"""

import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Self

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from presentator.adapters.build_worker import FIRST_RETRY_SECONDS, LONGEST_RETRY_SECONDS

SERVICE_ACCOUNT_DIRECTORY: Final = Path("/var/run/secrets/kubernetes.io/serviceaccount")
_HOST_VARIABLE: Final = "KUBERNETES_SERVICE_HOST"
_PORT_VARIABLE: Final = "KUBERNETES_SERVICE_PORT"
_ENDED_PHASES: Final = frozenset({"Succeeded", "Failed"})
_NO_RIGHT: Final = frozenset({401, 403})
_MINIMUM_REQUEST_SECONDS: Final = 0.001
_OK: Final = 200
_ENDED: Final = frozenset({200, 202, 404, 409})
_NOT_FOUND: Final = 404
_SERVER_ERROR: Final = 500
_MISSING: Final = "not reachable, {item} is missing"
_UNREADABLE: Final = "not reachable, ca.crt or namespace is unreadable"
_MALFORMED: Final = "the answer is malformed"
_SUCCESSOR_USED: Final = "successor was used without a step; not replacing it again"
_NO_UNUSED: Final = "none unused within {window:g} s (last seen: {seen})"
_NO_RIGHT_MESSAGE: Final = "the web may not {right} it ({code})"
_UNEXPECTED: Final = "the API answered {code} to {right}"
_SERVER_FAULT: Final = "server error {code}"
_GET: Final = "GET"
_DELETE: Final = "DELETE"
_BEARER_FILE: Final = "token"
_NAMESPACE_FILE: Final = "namespace"
_TIMEOUT: Final = "timeout"
_TRANSPORT: Final = "transport error"


class WorkerPodError(Exception):
    """The worker's pod cannot be judged or replaced; the start refuses."""


class PodState(StrEnum):
    """What one read of the pod says about its generation."""

    UNUSED = "unused"
    USED = "used"
    ENDING = "ending"


class _Container(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    restart_count: int = Field(default=0, alias="restartCount", strict=True)
    state: dict[str, object] = {}
    last_state: dict[str, object] = Field(default={}, alias="lastState")

    def has_ended(self) -> bool:
        return (
            self.restart_count > 0
            or "terminated" in self.state
            or "terminated" in self.last_state
        )


class _Metadata(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    uid: str = Field(min_length=1, strict=True)
    deletion_timestamp: str | None = Field(default=None, alias="deletionTimestamp")


class _Status(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    phase: str = ""
    containers: list[_Container] = Field(default=[], alias="containerStatuses")


class _Pod(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    metadata: _Metadata
    status: _Status = _Status()

    def state(self) -> PodState:
        if self.metadata.deletion_timestamp is not None:
            return PodState.ENDING
        if self.status.phase in _ENDED_PHASES or any(
            container.has_ended() for container in self.status.containers
        ):
            return PodState.USED
        return PodState.UNUSED


class _UnavailableError(Exception):
    """A fault the wait retries until its deadline."""


def _refused(reason: str) -> WorkerPodError:
    return WorkerPodError(f"the worker pod: {reason}")


def _absent(item: str) -> WorkerPodError:
    return _refused(_MISSING.format(item=item))


@dataclass(frozen=True)
class WorkerPod:
    """The one named pod of the build worker, as the web reads and ends it."""

    name: str
    base_url: str
    namespace: str
    token_file: Path
    transport: httpx2.BaseTransport
    clock: Callable[[], float]
    sleep: Callable[[float], None]

    @classmethod
    def in_cluster(
        cls,
        name: str,
        *,
        environment: Mapping[str, str],
        directory: Path = SERVICE_ACCOUNT_DIRECTORY,
    ) -> Self:
        """The pod as the cluster's own files and address name it."""
        host = environment.get(_HOST_VARIABLE)
        port = environment.get(_PORT_VARIABLE)
        for variable, value in ((_HOST_VARIABLE, host), (_PORT_VARIABLE, port)):
            if not value:
                raise _absent(variable)
        token_file = directory / _BEARER_FILE
        for file in (token_file, directory / "ca.crt", directory / _NAMESPACE_FILE):
            if not file.is_file():
                raise _absent(file.name)
        try:
            context = ssl.create_default_context(cafile=directory / "ca.crt")
            namespace = (directory / _NAMESPACE_FILE).read_text().strip()
        except OSError, ssl.SSLError:
            raise _refused(_UNREADABLE) from None
        if not namespace:
            raise _absent(_NAMESPACE_FILE)
        return cls(
            name=name,
            base_url=f"https://{host}:{port}",
            namespace=namespace,
            token_file=token_file,
            transport=httpx2.HTTPTransport(verify=context, trust_env=False),
            clock=time.monotonic,
            sleep=time.sleep,
        )

    def unused_generation(self, window_seconds: float) -> str:
        """The uid of an unused pod, ending used ones on the way, within the window."""
        deadline = self.clock() + window_seconds
        first_used: str | None = None
        pause = FIRST_RETRY_SECONDS
        waiting_for = "absent"
        with httpx2.Client(
            base_url=self.base_url, transport=self.transport, trust_env=False
        ) as client:
            while True:
                try:
                    pod = self._read(client, deadline)
                    state = None if pod is None else pod.state()
                    waiting_for = "absent" if state is None else state.value
                    if pod is not None and state is PodState.UNUSED:
                        return pod.metadata.uid
                    if pod is not None and state is PodState.USED:
                        uid = pod.metadata.uid
                        first_used = first_used or uid
                        if uid != first_used:
                            raise _refused(_SUCCESSOR_USED)
                        self._end(client, uid, deadline)
                except _UnavailableError as fault:
                    waiting_for = str(fault)
                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise _refused(
                        _NO_UNUSED.format(window=window_seconds, seen=waiting_for)
                    )
                self.sleep(min(pause, remaining))
                pause = min(pause * 2, LONGEST_RETRY_SECONDS)

    def _path(self) -> str:
        return f"/api/v1/namespaces/{self.namespace}/pods/{self.name}"

    def _read(self, client: httpx2.Client, deadline: float) -> _Pod | None:
        answer = self._send(client, _GET, deadline)
        if answer.status_code == _NOT_FOUND:
            return None
        if answer.status_code != _OK:
            raise self._unexpected(_GET, answer)
        try:
            return _Pod.model_validate_json(answer.content)
        except ValidationError:
            raise _refused(_MALFORMED) from None

    def _end(self, client: httpx2.Client, uid: str, deadline: float) -> None:
        options = {
            "kind": "DeleteOptions",
            "apiVersion": "v1",
            "preconditions": {"uid": uid},
        }
        answer = self._send(client, _DELETE, deadline, options)
        if answer.status_code in _ENDED:
            return
        raise self._unexpected(_DELETE, answer)

    @staticmethod
    def _unexpected(right: str, answer: httpx2.Response) -> Exception:
        if answer.status_code in _NO_RIGHT:
            return _refused(
                _NO_RIGHT_MESSAGE.format(right=right, code=answer.status_code)
            )
        if answer.status_code >= _SERVER_ERROR:
            return _UnavailableError(_SERVER_FAULT.format(code=answer.status_code))
        return _refused(_UNEXPECTED.format(code=answer.status_code, right=right))

    def _send(
        self,
        client: httpx2.Client,
        method: str,
        deadline: float,
        options: Mapping[str, object] | None = None,
    ) -> httpx2.Response:
        try:
            token = self.token_file.read_text().strip()
        except OSError:
            raise _absent(_BEARER_FILE) from None
        timeout = max(deadline - self.clock(), _MINIMUM_REQUEST_SECONDS)
        try:
            return client.request(
                method,
                self._path(),
                json=options,
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout,
            )
        except httpx2.TimeoutException:
            raise _UnavailableError(_TIMEOUT) from None
        except httpx2.TransportError:
            raise _UnavailableError(_TRANSPORT) from None
