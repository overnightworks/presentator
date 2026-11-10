"""The composition root builds a deck through the build worker when told to."""

import os
import socket
from datetime import timedelta
from pathlib import Path

import httpx2
import pytest

from presentator.adapters.build_wire import SOCKET_NAME
from presentator.adapters.build_worker import CONNECT_WINDOW_SECONDS, WorkerToolchain
from presentator.adapters.worker_pod import WorkerPod
from presentator.host import main
from presentator.host.config import ConfigurationError, load_settings
from presentator.host.worker_serving import emergency_end

BUILD_TIMEOUT_SECONDS = 123.0
OUTPUT_MEGABYTES = 77


def a_worker_run(environment: pytest.MonkeyPatch, socket_path: Path) -> None:
    environment.setenv("PRESENTATOR_BUILD_RUNNER", "worker")
    environment.setenv("PRESENTATOR_BUILD_WORKER_SOCKET", str(socket_path))
    environment.setenv("PRESENTATOR_BUILD_TIMEOUT_SECONDS", str(BUILD_TIMEOUT_SECONDS))
    environment.setenv("PRESENTATOR_BUILD_OUTPUT_MEGABYTES", str(OUTPUT_MEGABYTES))


def test_an_instance_whose_worker_socket_is_not_the_workers_refuses_to_start_at_once(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cell B14: the branch of the root, reached without the connect window."""
    volume = tmp_path / "volume"
    volume.mkdir(mode=0o700)
    placeholder = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    placeholder.bind(str(volume / SOCKET_NAME))
    a_worker_run(environment, volume / SOCKET_NAME)

    settings = load_settings()

    with placeholder, pytest.raises(ConfigurationError, match="build worker"):
        main.build_instance(settings)


def test_an_instance_starts_on_a_worker_that_answers_with_the_workers_own_bounds(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Review need Q4: the seam fields reach the client as one production value."""
    built: list[WorkerToolchain] = []

    def answering(toolchain: WorkerToolchain) -> bool:
        built.append(toolchain)
        return True

    a_worker_run(environment, tmp_path / "worker.sock")
    environment.setattr(WorkerToolchain, "is_answering", answering)

    main.build_instance(load_settings())

    (toolchain,) = built
    assert toolchain.socket_path == tmp_path / "worker.sock"
    assert toolchain.megabytes == OUTPUT_MEGABYTES
    assert toolchain.reply_bound_seconds == emergency_end(0.0, BUILD_TIMEOUT_SECONDS)
    assert (toolchain.expected_uid, toolchain.expected_gid) == (0, os.getegid())


POD_NAME = "build-worker-0"
BOUND = timedelta(seconds=BUILD_TIMEOUT_SECONDS)


def a_pod_answer(uid: str, *, restarts: int) -> httpx2.Response:
    container: dict[str, object] = {"restartCount": restarts}
    return httpx2.Response(
        200,
        json={"metadata": {"uid": uid}, "status": {"containerStatuses": [container]}},
    )


class ClusterDouble:
    """A fake API and clock for one start, and the order of what happened."""

    def __init__(self, tmp_path: Path, *, answers: list[httpx2.Response]) -> None:
        """The pod's reads in turn; a deletion is always accepted."""
        self.events: list[str] = []
        self.now = 0.0
        self._answers = answers
        (tmp_path / "token").write_text("a-token")
        self.pod = WorkerPod(
            name=POD_NAME,
            base_url="https://10.0.0.1:443",
            namespace="presentator",
            token_file=tmp_path / "token",
            transport=httpx2.MockTransport(self._answer),
            clock=lambda: self.now,
            sleep=self._sleep,
        )

    def _answer(self, request: httpx2.Request) -> httpx2.Response:
        self.events.append(request.method)
        if request.method == "DELETE":
            return httpx2.Response(200, json={})
        return self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]

    def _sleep(self, seconds: float) -> None:
        self.now += seconds


def test_a_start_in_a_cluster_replaces_a_used_pod_before_it_says_hello(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cluster = ClusterDouble(
        tmp_path,
        answers=[a_pod_answer("a", restarts=1), a_pod_answer("b", restarts=0)],
    )

    def answering(_toolchain: WorkerToolchain) -> bool:
        cluster.events.append("HELLO")
        return True

    a_worker_run(environment, tmp_path / "worker.sock")
    environment.setenv("PRESENTATOR_BUILD_WORKER_POD", POD_NAME)
    environment.setattr(WorkerToolchain, "is_answering", answering)

    main.the_build_worker(load_settings(), bound=BOUND, pod=cluster.pod)

    assert cluster.events == ["GET", "DELETE", "GET", "HELLO"]


def test_a_start_whose_pod_never_becomes_unused_refuses_within_the_connect_window(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cluster = ClusterDouble(tmp_path, answers=[httpx2.Response(503)])
    said_hello: list[WorkerToolchain] = []

    def answering(toolchain: WorkerToolchain) -> bool:
        said_hello.append(toolchain)
        return True

    a_worker_run(environment, tmp_path / "worker.sock")
    environment.setenv("PRESENTATOR_BUILD_WORKER_POD", POD_NAME)
    environment.setattr(WorkerToolchain, "is_answering", answering)

    with pytest.raises(ConfigurationError, match="worker pod"):
        main.the_build_worker(load_settings(), bound=BOUND, pod=cluster.pod)

    assert cluster.now == CONNECT_WINDOW_SECONDS
    assert not said_hello


def test_a_start_without_the_setting_makes_no_request_to_a_cluster(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cluster = ClusterDouble(tmp_path, answers=[a_pod_answer("a", restarts=1)])

    def answering(_toolchain: WorkerToolchain) -> bool:
        return True

    a_worker_run(environment, tmp_path / "worker.sock")
    environment.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    environment.setattr(WorkerToolchain, "is_answering", answering)

    main.the_build_worker(load_settings(), bound=BOUND, pod=cluster.pod)

    assert cluster.events == []


def test_a_start_in_a_cluster_without_its_address_refuses_naming_it(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a_worker_run(environment, tmp_path / "worker.sock")
    environment.setenv("PRESENTATOR_BUILD_WORKER_POD", POD_NAME)
    environment.delenv("KUBERNETES_SERVICE_HOST", raising=False)

    with pytest.raises(ConfigurationError, match="KUBERNETES_SERVICE_HOST"):
        main.the_build_worker(load_settings(), bound=BOUND)


@pytest.mark.parametrize("name", ["Build-Worker", "-worker", "worker_0", "", "a" * 254])
def test_a_pod_name_that_is_no_dns_subdomain_refuses_to_start(
    name: str, environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a_worker_run(environment, tmp_path / "worker.sock")
    environment.setenv("PRESENTATOR_BUILD_WORKER_POD", name)

    with pytest.raises(ConfigurationError, match="build_worker_pod"):
        load_settings()
