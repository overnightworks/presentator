"""The web replaces a used worker pod, naming the generation it ends."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from presentator.adapters.build_worker import CONNECT_WINDOW_SECONDS
from presentator.adapters.worker_pod import WorkerPod, WorkerPodError

WINDOW_SECONDS = 30.0
POD_NAME = "build-worker-0"
NAMESPACE = "presentator"
TOKEN = "token-of-this-test"  # noqa: S105 - a value this module invents
POD_PATH = f"/api/v1/namespaces/{NAMESPACE}/pods/{POD_NAME}"

type Answer = httpx2.Response | Exception


def a_pod(  # noqa: PLR0913 - one keyword per field of a pod's status
    uid: str,
    *,
    phase: str = "Running",
    restarts: int = 0,
    state: Mapping[str, object] | None = None,
    last_state: Mapping[str, object] | None = None,
    deleting: bool = False,
) -> httpx2.Response:
    metadata: dict[str, object] = {"name": POD_NAME, "uid": uid}
    if deleting:
        metadata["deletionTimestamp"] = "2026-11-10T12:00:00Z"
    container = {
        "name": "worker",
        "restartCount": restarts,
        "state": dict(state or {"running": {}}),
        "lastState": dict(last_state or {}),
    }
    status = {"phase": phase, "containerStatuses": [container]}
    return httpx2.Response(200, json={"metadata": metadata, "status": status})


def a_status(code: int) -> httpx2.Response:
    return httpx2.Response(code, json={"kind": "Status", "code": code})


@dataclass
class FakeClock:
    """A clock whose sleep moves it."""

    now: float = 1000.0
    slept: list[float] = field(default_factory=list[float])

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class FakeApi:
    """Answers the pod's reads and deletions in turn; the last answer repeats."""

    def __init__(
        self, reads: Sequence[Answer], deletes: Sequence[Answer] | None = None
    ) -> None:
        """Reads and deletions each answer from their own script."""
        self._reads = list(reads)
        self._deletes = list(deletes or [httpx2.Response(200, json={})])
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        queue = self._reads if request.method == "GET" else self._deletes
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def methods(self) -> list[str]:
        return [request.method for request in self.requests]

    def deleted(self) -> list[object]:
        return [
            json.loads(request.content)["preconditions"]["uid"]
            for request in self.requests
            if request.method == "DELETE"
        ]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "token"
    path.write_text(TOKEN)
    return path


def a_pod_owner(api: FakeApi, clock: FakeClock, token_file: Path) -> WorkerPod:
    return WorkerPod(
        name=POD_NAME,
        base_url="https://10.0.0.1:443",
        namespace=NAMESPACE,
        token_file=token_file,
        transport=httpx2.MockTransport(api),
        clock=clock,
        sleep=clock.sleep,
    )


def wait(api: FakeApi, clock: FakeClock, token_file: Path) -> str:
    return a_pod_owner(api, clock, token_file).unused_generation(WINDOW_SECONDS)


def test_an_unused_pod_is_returned_without_a_deletion(
    clock: FakeClock, token_file: Path
) -> None:
    api = FakeApi([a_pod("uid-a")])

    assert wait(api, clock, token_file) == "uid-a"
    assert api.methods() == ["GET"]
    assert api.requests[0].url.path == POD_PATH


def test_a_pod_whose_init_container_still_runs_is_unused(
    clock: FakeClock, token_file: Path
) -> None:
    pod = httpx2.Response(
        200,
        json={"metadata": {"uid": "uid-a"}, "status": {"phase": "Pending"}},
    )

    assert wait(FakeApi([pod]), clock, token_file) == "uid-a"


@pytest.mark.parametrize(
    "used",
    [
        pytest.param(a_pod("uid-a", restarts=1), id="restarted"),
        pytest.param(
            a_pod("uid-a", state={"terminated": {"exitCode": 0}}), id="terminated"
        ),
        pytest.param(
            a_pod("uid-a", last_state={"terminated": {"exitCode": 1}}),
            id="last-terminated",
        ),
        pytest.param(a_pod("uid-a", phase="Failed"), id="failed"),
        pytest.param(a_pod("uid-a", phase="Succeeded"), id="succeeded"),
    ],
)
def test_a_used_pod_is_ended_by_its_uid_and_the_successor_is_returned(
    used: httpx2.Response, clock: FakeClock, token_file: Path
) -> None:
    api = FakeApi([used, a_pod("uid-b")])

    assert wait(api, clock, token_file) == "uid-b"
    assert api.deleted() == ["uid-a"]


def test_the_deletion_names_the_generation_and_forces_nothing(
    clock: FakeClock, token_file: Path
) -> None:
    api = FakeApi([a_pod("uid-a", restarts=2), a_pod("uid-b")])

    wait(api, clock, token_file)

    (deletion,) = [request for request in api.requests if request.method == "DELETE"]
    assert deletion.url.path == POD_PATH
    assert json.loads(deletion.content) == {
        "kind": "DeleteOptions",
        "apiVersion": "v1",
        "preconditions": {"uid": "uid-a"},
    }


@pytest.mark.parametrize("phase", ["Running", "Failed", "Succeeded"])
def test_a_pod_with_a_deletion_timestamp_is_waited_for_not_deleted(
    phase: str, clock: FakeClock, token_file: Path
) -> None:
    api = FakeApi(
        [a_pod("uid-a", phase=phase, restarts=1, deleting=True), a_pod("uid-b")]
    )

    assert wait(api, clock, token_file) == "uid-b"
    assert api.methods() == ["GET", "GET"]
    assert clock.slept


def test_an_absent_pod_is_waited_for_not_deleted(
    clock: FakeClock, token_file: Path
) -> None:
    api = FakeApi([a_status(404), a_pod("uid-b")])

    assert wait(api, clock, token_file) == "uid-b"
    assert api.methods() == ["GET", "GET"]


@pytest.mark.parametrize("code", [409, 404])
def test_a_deletion_answered_as_already_ended_lets_the_wait_go_on(
    code: int, clock: FakeClock, token_file: Path
) -> None:
    api = FakeApi([a_pod("uid-a", restarts=1), a_pod("uid-b")], [a_status(code)])

    assert wait(api, clock, token_file) == "uid-b"
    assert api.deleted() == ["uid-a"]


def test_a_deletion_whose_answer_was_lost_never_deletes_the_successor(
    clock: FakeClock, token_file: Path
) -> None:
    api = FakeApi(
        [a_pod("uid-a", restarts=1), a_pod("uid-b")],
        [httpx2.ReadError("answer lost")],
    )

    assert wait(api, clock, token_file) == "uid-b"
    assert api.deleted() == ["uid-a"]


def test_a_deletion_that_was_not_applied_is_retried_with_the_same_uid(
    clock: FakeClock, token_file: Path
) -> None:
    api = FakeApi(
        [a_pod("uid-a", restarts=1), a_pod("uid-a", restarts=1), a_pod("uid-b")],
        [httpx2.ConnectError("refused"), httpx2.Response(202, json={})],
    )

    assert wait(api, clock, token_file) == "uid-b"
    assert api.deleted() == ["uid-a", "uid-a"]


def test_a_successor_that_is_used_too_is_not_deleted_and_the_start_refuses(
    clock: FakeClock, token_file: Path
) -> None:
    api = FakeApi([a_pod("uid-a", restarts=1), a_pod("uid-b", restarts=1)])

    with pytest.raises(WorkerPodError, match="worker pod"):
        wait(api, clock, token_file)

    assert api.deleted() == ["uid-a"]


@pytest.mark.parametrize(
    ("answer", "fault"),
    [
        pytest.param(httpx2.ConnectError("no route"), "transport", id="transport"),
        pytest.param(httpx2.ReadTimeout("slow"), "timeout", id="timeout"),
        pytest.param(a_status(503), "server error", id="server-error"),
    ],
)
def test_an_api_that_stays_unavailable_refuses_at_the_bound_naming_the_fault(
    answer: Answer, fault: str, clock: FakeClock, token_file: Path
) -> None:
    api = FakeApi([answer])
    started = clock.now

    with pytest.raises(WorkerPodError, match=fault):
        wait(api, clock, token_file)

    assert clock.now - started == WINDOW_SECONDS
    assert set(api.methods()) == {"GET"}


def test_a_pod_that_stays_absent_refuses_at_the_bound(
    clock: FakeClock, token_file: Path
) -> None:
    with pytest.raises(WorkerPodError, match="absent"):
        wait(FakeApi([a_status(404)]), clock, token_file)


@pytest.mark.parametrize("code", [401, 403])
@pytest.mark.parametrize("failing", ["GET", "DELETE"])
def test_a_missing_right_refuses_at_once_without_the_token_in_the_message(
    code: int, failing: str, clock: FakeClock, token_file: Path
) -> None:
    reads = [a_status(code)] if failing == "GET" else [a_pod("uid-a", restarts=1)]
    api = FakeApi(reads, [a_status(code)])

    with pytest.raises(WorkerPodError) as refused:
        wait(api, clock, token_file)

    assert failing in str(refused.value)
    assert TOKEN not in str(refused.value)
    assert not clock.slept


def test_the_token_is_read_again_for_every_request(
    clock: FakeClock, token_file: Path
) -> None:
    api = FakeApi([a_pod("uid-a", restarts=1), a_pod("uid-b")])
    real = api.__call__

    def rotating(request: httpx2.Request) -> httpx2.Response:
        answer = real(request)
        token_file.write_text("rotated-token")
        return answer

    pod = WorkerPod(
        name=POD_NAME,
        base_url="https://10.0.0.1:443",
        namespace=NAMESPACE,
        token_file=token_file,
        transport=httpx2.MockTransport(rotating),
        clock=clock,
        sleep=clock.sleep,
    )
    pod.unused_generation(WINDOW_SECONDS)

    sent = [request.headers["authorization"] for request in api.requests]
    assert sent[0] == f"Bearer {TOKEN}"
    assert set(sent[1:]) == {"Bearer rotated-token"}


def test_no_request_waits_longer_than_the_time_left(
    clock: FakeClock, token_file: Path
) -> None:
    api = FakeApi([httpx2.ConnectError("no route")])

    with pytest.raises(WorkerPodError):
        wait(api, clock, token_file)

    timeouts = [request.extensions["timeout"]["read"] for request in api.requests]
    assert max(timeouts) <= WINDOW_SECONDS
    assert min(timeouts) < 1.0


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(httpx2.Response(200, json={"metadata": {}}), id="no-uid"),
        pytest.param(
            httpx2.Response(200, json={"metadata": {"uid": 7}}), id="uid-type"
        ),
        pytest.param(
            httpx2.Response(
                200,
                json={
                    "metadata": {"uid": "uid-a"},
                    "status": {"containerStatuses": [{"restartCount": "many"}]},
                },
            ),
            id="restart-count-type",
        ),
        pytest.param(httpx2.Response(200, content=b"<html>"), id="not-json"),
        pytest.param(a_status(400), id="unexpected-status"),
    ],
)
def test_a_malformed_answer_refuses_and_deletes_nothing(
    answer: httpx2.Response, clock: FakeClock, token_file: Path
) -> None:
    api = FakeApi([answer])

    with pytest.raises(WorkerPodError):
        wait(api, clock, token_file)

    assert api.methods() == ["GET"]


def an_environment() -> dict[str, str]:
    return {"KUBERNETES_SERVICE_HOST": "10.0.0.1", "KUBERNETES_SERVICE_PORT": "443"}


def a_serviceaccount_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "serviceaccount"
    directory.mkdir()
    (directory / "token").write_text(TOKEN)
    (directory / "namespace").write_text(NAMESPACE)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test ca")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    (directory / "ca.crt").write_bytes(
        certificate.public_bytes(serialization.Encoding.PEM)
    )
    return directory


def test_the_pod_is_reached_through_the_in_cluster_files_and_address(
    tmp_path: Path,
) -> None:
    directory = a_serviceaccount_directory(tmp_path)

    pod = WorkerPod.in_cluster(
        POD_NAME, environment=an_environment(), directory=directory
    )

    assert pod.base_url == "https://10.0.0.1:443"
    assert pod.namespace == NAMESPACE
    assert pod.token_file == directory / "token"


@pytest.mark.parametrize(
    "missing",
    [
        "KUBERNETES_SERVICE_HOST",
        "KUBERNETES_SERVICE_PORT",
        "token",
        "ca.crt",
        "namespace",
    ],
)
def test_a_missing_address_or_file_refuses_naming_what_is_missing(
    missing: str, tmp_path: Path
) -> None:
    directory = a_serviceaccount_directory(tmp_path)
    environment = an_environment()
    if missing in environment:
        del environment[missing]
    else:
        (directory / missing).unlink()

    with pytest.raises(WorkerPodError, match=missing):
        WorkerPod.in_cluster(POD_NAME, environment=environment, directory=directory)


def test_the_window_of_a_wait_is_the_one_the_caller_names(
    clock: FakeClock, token_file: Path
) -> None:
    api = FakeApi([httpx2.ConnectError("no route")])
    started = clock.now

    with pytest.raises(WorkerPodError):
        a_pod_owner(api, clock, token_file).unused_generation(CONNECT_WINDOW_SECONDS)

    assert clock.now - started == CONNECT_WINDOW_SECONDS
