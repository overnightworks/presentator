"""The build worker refuses to start while it has any route out."""

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from presentator.adapters import rtnetlink
from presentator.adapters.rtnetlink import Address, Link, Route, Topology
from presentator.host import worker_self_check
from presentator.host.worker_self_check import Predicate, Snapshot
from tests.presentator.host.support.sealed_snapshot import (
    LOOPBACK_INDEX,
    read_sealed_snapshot,
    sealed_snapshot,
    sealed_topology,
    stage_sealed_root,
)

OTHER_INDEX = LOOPBACK_INDEX + 1


def with_topology(**changes: tuple[object, ...]) -> Callable[[Topology], Topology]:
    return lambda topology: replace(topology, **changes)


def with_extra(**extra: tuple[object, ...]) -> Callable[[Topology], Topology]:
    return lambda topology: replace(
        topology,
        **{name: (*getattr(topology, name), *more) for name, more in extra.items()},
    )


def address(link_index: int, text: str, prefix_length: int) -> Address:
    ip_address = ipaddress.ip_address(text)
    family = (
        socket.AF_INET
        if isinstance(ip_address, ipaddress.IPv4Address)
        else socket.AF_INET6
    )
    return Address(family, prefix_length, link_index, ip_address)


def route(
    output_link_index: int | None = LOOPBACK_INDEX,
    prefix_length: int = 8,
    *,
    family: int = socket.AF_INET,
    has_next_hop: bool = False,
) -> Route:
    return Route(family, 254, prefix_length, output_link_index, has_next_hop)


def lo_with(flags: int) -> tuple[Link, ...]:
    return (Link(LOOPBACK_INDEX, "lo", flags),)


def only_ipv4() -> Callable[[Topology], Topology]:
    return lambda topology: replace(
        topology,
        addresses=topology.addresses[:1],
        routes=topology.routes[:1],
    )


def unchanged(topology: Topology) -> Topology:
    return topology


PASSING = {
    "loopback with both addresses": unchanged,
    "loopback with IPv4 only": only_ipv4(),
    "loopback with no address": with_topology(addresses=(), routes=()),
}
REFUSED = {
    "loopback without the loopback flag": with_topology(links=lo_with(0x1)),
    "second link down": with_extra(links=(Link(OTHER_INDEX, "eth0", 0),)),
    "second link up": with_extra(links=(Link(OTHER_INDEX, "eth0", 0x1),)),
    "link named otherwise": with_topology(
        links=(Link(LOOPBACK_INDEX, "dummy0", rtnetlink.IFF_LOOPBACK),)
    ),
    "no link": with_topology(links=()),
    "link-local address on loopback": with_extra(
        addresses=(address(LOOPBACK_INDEX, "fe80::1", 64),)
    ),
    "private address on loopback": with_extra(
        addresses=(address(LOOPBACK_INDEX, "10.0.0.1", 8),)
    ),
    "loopback address with another prefix": with_extra(
        addresses=(address(LOOPBACK_INDEX, "127.0.0.1", 32),)
    ),
    "address on another link": with_extra(
        addresses=(address(OTHER_INDEX, "127.0.0.1", 8),)
    ),
    "IPv4 default route": with_extra(routes=(route(prefix_length=0),)),
    "IPv6 default route": with_extra(
        routes=(route(prefix_length=0, family=socket.AF_INET6),)
    ),
    "route without an output link": with_extra(routes=(route(None),)),
    "route to another link": with_extra(routes=(route(OTHER_INDEX),)),
    "route with a next hop": with_extra(routes=(route(has_next_hop=True),)),
}


def judged(topology: Topology) -> Predicate | None:
    return worker_self_check.first_failing_predicate(
        replace(sealed_snapshot(), topology=topology)
    )


@pytest.mark.parametrize("change", PASSING.values(), ids=PASSING.keys())
def test_a_loopback_only_topology_passes_every_predicate(
    change: Callable[[Topology], Topology],
) -> None:
    assert judged(change(sealed_topology())) is None


@pytest.mark.parametrize("change", REFUSED.values(), ids=REFUSED.keys())
def test_any_other_topology_refuses_as_no_route(
    change: Callable[[Topology], Topology],
) -> None:
    assert judged(change(sealed_topology())) == Predicate.NO_ROUTE


def test_no_route_is_judged_after_mounts_and_before_resource_bounds() -> None:
    snapshot = replace(
        sealed_snapshot(),
        root_read_only=False,
        topology=with_extra(routes=(route(prefix_length=0),))(sealed_topology()),
        pids_max="max",
    )

    assert worker_self_check.first_failing_predicate(snapshot) == Predicate.MOUNTS
    mounted = replace(snapshot, root_read_only=True)
    assert worker_self_check.first_failing_predicate(mounted) == Predicate.NO_ROUTE


@pytest.fixture
def sealed_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return stage_sealed_root(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    "failure",
    [
        rtnetlink.DumpRejectedError(rtnetlink.Rejection.DUMP_INTERRUPTED),
        PermissionError("socket"),
    ],
    ids=["dump rejected", "socket does not open"],
)
def test_an_unreadable_topology_marks_no_route_unreadable(
    sealed_root: Path, failure: OSError
) -> None:
    def failing() -> Topology:
        raise failure

    snapshot = read_sealed_snapshot(sealed_root, failing)

    assert snapshot.unreadable == {Predicate.NO_ROUTE}
    sealed = replace(sealed_snapshot(), unreadable=snapshot.unreadable)
    assert worker_self_check.first_failing_predicate(sealed) == Predicate.NO_ROUTE


def test_a_second_link_listed_only_in_the_device_table_is_not_judged_by_mounts(
    sealed_root: Path,
) -> None:
    (sealed_root / "proc/net").mkdir(parents=True)
    (sealed_root / "proc/net/dev").write_text(
        "Inter-|\n face |\n    lo: 0\n  eth0: 0\n"
    )

    snapshot: Snapshot = read_sealed_snapshot(sealed_root)

    judged_alone = replace(
        sealed_snapshot(), mountinfo=snapshot.mountinfo, topology=snapshot.topology
    )
    assert worker_self_check.first_failing_predicate(judged_alone) is None
