"""The egress probe names how each attempt of a user ended: sent or an errno."""

import socket
from collections.abc import Callable
from types import ModuleType

import pytest

LOOPBACK = "127.0.0.1"
NO_PORT = 0


@pytest.fixture
def egress_probe(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("egress_probe", "probe_build_worker")


def test_a_datagram_that_leaves_is_sent(egress_probe: ModuleType) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind((LOOPBACK, 0))

        end = egress_probe.send_datagram(receiver.getsockname())

    assert end == "sent"


def test_a_datagram_the_kernel_refuses_ends_with_the_errno_name(
    egress_probe: ModuleType,
) -> None:
    assert egress_probe.send_datagram((LOOPBACK, NO_PORT)) == "EINVAL"


def test_a_connection_to_a_listener_is_connected(egress_probe: ModuleType) -> None:
    with socket.socket() as listener:
        listener.bind((LOOPBACK, 0))
        listener.listen()

        end = egress_probe.connect(listener.getsockname())

    assert end == "connected"


def test_a_connection_to_a_closed_port_ends_with_the_errno_name(
    egress_probe: ModuleType,
) -> None:
    with socket.socket() as closed:
        closed.bind((LOOPBACK, 0))
        address = closed.getsockname()

    assert egress_probe.connect(address) == "ECONNREFUSED"
