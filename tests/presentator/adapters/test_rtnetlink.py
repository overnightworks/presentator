"""A dump the kernel could send is read whole; every flaw in one rejects the read."""

import ipaddress
import socket
import struct
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from presentator.adapters.rtnetlink import (
    Address,
    DumpRejectedError,
    Link,
    ReadBounds,
    Rejection,
    Route,
    Topology,
    read_topology,
)

# Constants of linux/netlink.h and linux/rtnetlink.h, written out so the
# expected wire format never comes from the code under test.
NLMSG_ERROR, NLMSG_DONE, NLMSG_OVERRUN = 2, 3, 4
RTM_GETLINK, RTM_NEWLINK = 18, 16
RTM_GETADDR, RTM_NEWADDR = 22, 20
RTM_GETROUTE, RTM_NEWROUTE = 26, 24
NLM_F_MULTI, NLM_F_DUMP_INTR = 0x2, 0x10
IFLA_IFNAME, IFA_ADDRESS, IFA_LOCAL = 3, 1, 2
RTA_OIF, RTA_GATEWAY, RTA_MULTIPATH, RTA_TABLE, RTA_VIA, RTA_NH_ID = 4, 5, 9, 15, 18, 30
IFF_LOOPBACK_UP = 0x49


def attribute(kind: int, value: bytes) -> bytes:
    length = 4 + len(value)
    return struct.pack("=HH", length, kind) + value + b"\0" * (-length % 4)


def message(
    kind: int, sequence: int, payload: bytes, flags: int = NLM_F_MULTI
) -> bytes:
    length = 16 + len(payload)
    header = struct.pack("=IHHII", length, kind, flags, sequence, 4242)
    return header + payload + b"\0" * (-length % 4)


def link_payload(index: int = 1, name: bytes = b"lo\0") -> bytes:
    body = struct.pack("=BxHiII", 0, 772, index, IFF_LOOPBACK_UP, 0)
    return body + attribute(IFLA_IFNAME, name)


def address_payload(
    family: int, prefix: int, value: bytes, kind: int = IFA_ADDRESS
) -> bytes:
    return struct.pack("=BBBBI", family, prefix, 0, 254, 1) + attribute(kind, value)


def route_payload(family: int, prefix: int, *attributes: bytes) -> bytes:
    body = struct.pack("=BBBBBBBBI", family, prefix, 0, 0, 254, 2, 253, 1, 0)
    return body + b"".join(attributes)


def done(sequence: int, status: int = 0) -> bytes:
    return message(NLMSG_DONE, sequence, struct.pack("=i", status))


def integer(value: int) -> bytes:
    return struct.pack("=I", value)


@dataclass(frozen=True)
class Reply:
    """One datagram the fake kernel hands back."""

    data: bytes
    flags: int = 0
    sender: int = 0


def good_replies(request_type: int, family: int, sequence: int) -> list[Reply]:
    if request_type == RTM_GETLINK:
        facts = [message(RTM_NEWLINK, sequence, link_payload())]
    elif request_type == RTM_GETADDR and family == socket.AF_INET:
        facts = [
            message(RTM_NEWADDR, sequence, address_payload(2, 8, bytes([127, 0, 0, 1])))
        ]
    elif request_type == RTM_GETADDR:
        loopback = ipaddress.ip_address("::1").packed
        facts = [message(RTM_NEWADDR, sequence, address_payload(10, 128, loopback))]
    elif family == socket.AF_INET:
        route = route_payload(2, 8, attribute(RTA_OIF, integer(1)))
        facts = [message(RTM_NEWROUTE, sequence, route)]
    else:
        route = route_payload(
            10, 128, attribute(RTA_TABLE, integer(255)), attribute(RTA_OIF, integer(1))
        )
        facts = [message(RTM_NEWROUTE, sequence, route)]
    return [Reply(b"".join(facts) + done(sequence))]


Script = Callable[[int, int, int], list[Reply]]


@dataclass
class FakeKernelSocket:
    """Answers each dump request from a script; an empty queue is a read timeout."""

    script: Script = good_replies
    queue: list[Reply] = field(default_factory=list[Reply])
    closed: bool = False

    def settimeout(self, value: float | None, /) -> None:
        pass

    def send(self, data: bytes, /) -> int:
        _, request_type, _, sequence, _ = struct.unpack_from("=IHHII", data)
        family = data[16]
        self.queue.extend(self.script(request_type, family, sequence))
        return len(data)

    def recvmsg(
        self, _buffer_size: int, /
    ) -> tuple[bytes, list[Any], int, tuple[int, int]]:
        if not self.queue:
            raise TimeoutError
        reply = self.queue.pop(0)
        return reply.data, [], reply.flags, (reply.sender, 0)

    def close(self) -> None:
        self.closed = True


def read(
    script: Script = good_replies,
    bounds: ReadBounds | None = None,
    clock: Callable[[], float] = lambda: 0.0,
) -> tuple[Topology, FakeKernelSocket]:
    kernel_socket = FakeKernelSocket(script)
    topology = read_topology(lambda: kernel_socket, bounds or ReadBounds(), clock)
    return topology, kernel_socket


def first_dump_altered(alter: Alter) -> Script:
    def script(request_type: int, family: int, sequence: int) -> list[Reply]:
        replies = good_replies(request_type, family, sequence)
        if request_type == RTM_GETLINK:
            return [alter(replies[0].data, sequence)]
        return replies

    return script


def test_a_good_loopback_only_dump_yields_its_links_addresses_and_routes() -> None:
    topology, kernel_socket = read()

    assert topology.links == (Link(1, "lo", IFF_LOOPBACK_UP),)
    assert topology.addresses == (
        Address(socket.AF_INET, 8, 1, ipaddress.ip_address("127.0.0.1")),
        Address(socket.AF_INET6, 128, 1, ipaddress.ip_address("::1")),
    )
    assert topology.routes == (
        Route(socket.AF_INET, 254, 8, 1, has_next_hop=False),
        Route(socket.AF_INET6, 255, 128, 1, has_next_hop=False),
    )
    assert kernel_socket.closed


@pytest.mark.parametrize(
    "next_hop_attribute",
    [
        attribute(RTA_GATEWAY, bytes(4)),
        attribute(RTA_VIA, bytes(8)),
        attribute(RTA_MULTIPATH, bytes(16)),
        attribute(RTA_NH_ID, integer(7)),
    ],
    ids=["gateway", "via", "multipath", "nexthop id"],
)
def test_a_route_naming_a_next_hop_says_so(next_hop_attribute: bytes) -> None:
    def script(request_type: int, family: int, sequence: int) -> list[Reply]:
        if request_type == RTM_GETROUTE and family == socket.AF_INET:
            route = route_payload(
                2, 0, attribute(RTA_OIF, integer(3)), next_hop_attribute
            )
            return [Reply(message(RTM_NEWROUTE, sequence, route) + done(sequence))]
        return good_replies(request_type, family, sequence)

    topology, _ = read(script)

    route = topology.routes[0]
    assert (route.destination_prefix_length, route.output_link_index) == (0, 3)
    assert route.has_next_hop


def test_a_local_address_wins_over_a_peer_address() -> None:
    def script(request_type: int, family: int, sequence: int) -> list[Reply]:
        if request_type == RTM_GETADDR and family == socket.AF_INET:
            payload = address_payload(2, 32, bytes([10, 0, 0, 1]), IFA_LOCAL)
            payload += attribute(IFA_ADDRESS, bytes([10, 0, 0, 2]))
            return [Reply(message(RTM_NEWADDR, sequence, payload) + done(sequence))]
        return good_replies(request_type, family, sequence)

    topology, _ = read(script)

    assert topology.addresses[0].ip_address == ipaddress.ip_address("10.0.0.1")


Alter = Callable[[bytes, int], Reply]


def seq_swapped(data: bytes, sequence: int) -> Reply:
    old = struct.pack("=II", sequence, 4242)
    return Reply(data.replace(old, struct.pack("=II", sequence + 9, 4242)))


REJECTIONS: dict[str, tuple[Alter, Rejection]] = {
    "dump interrupted": (
        lambda _data, sequence: Reply(
            message(
                RTM_NEWLINK, sequence, link_payload(), NLM_F_MULTI | NLM_F_DUMP_INTR
            )
            + done(sequence)
        ),
        Rejection.DUMP_INTERRUPTED,
    ),
    "overrun": (
        lambda _data, sequence: Reply(message(NLMSG_OVERRUN, sequence, b"")),
        Rejection.DUMP_OVERRUN,
    ),
    "error message": (
        lambda _data, sequence: Reply(
            message(NLMSG_ERROR, sequence, struct.pack("=i", -1) + bytes(16))
        ),
        Rejection.KERNEL_REPORTED_AN_ERROR,
    ),
    "unsuccessful done": (
        lambda data, sequence: Reply(data[:-20] + done(sequence, -5)),
        Rejection.DUMP_ENDED_UNSUCCESSFULLY,
    ),
    "done without status": (
        lambda _data, sequence: Reply(message(NLMSG_DONE, sequence, b"")),
        Rejection.DUMP_ENDED_UNSUCCESSFULLY,
    ),
    "receive truncated": (
        lambda data, _sequence: Reply(data, flags=socket.MSG_TRUNC),
        Rejection.TRUNCATED_RECEIVE,
    ),
    "short message": (
        lambda data, _sequence: Reply(data[:30]),
        Rejection.SHORT_NETLINK_MESSAGE,
    ),
    "short header": (
        lambda data, _sequence: Reply(data[:10]),
        Rejection.SHORT_NETLINK_HEADER,
    ),
    "short attribute": (
        lambda _data, sequence: Reply(
            message(RTM_NEWLINK, sequence, link_payload()[:16] + b"\x02\x00\x03\x00")
            + done(sequence)
        ),
        Rejection.MALFORMED_ATTRIBUTE,
    ),
    "attribute beyond message": (
        lambda _data, sequence: Reply(
            message(
                RTM_NEWLINK, sequence, link_payload()[:16] + struct.pack("=HH", 40, 3)
            )
            + done(sequence)
        ),
        Rejection.MALFORMED_ATTRIBUTE,
    ),
    "attribute header cut": (
        lambda _data, sequence: Reply(
            message(RTM_NEWLINK, sequence, link_payload()[:16] + b"\x08\x00")
            + done(sequence)
        ),
        Rejection.SHORT_ATTRIBUTE_HEADER,
    ),
    "short link body": (
        lambda _data, sequence: Reply(
            message(RTM_NEWLINK, sequence, bytes(8)) + done(sequence)
        ),
        Rejection.SHORT_MESSAGE_BODY,
    ),
    "link without name": (
        lambda _data, sequence: Reply(
            message(RTM_NEWLINK, sequence, link_payload()[:16]) + done(sequence)
        ),
        Rejection.LINK_WITHOUT_A_NAME,
    ),
    "wrong sequence": (seq_swapped, Rejection.MESSAGE_OF_ANOTHER_SEQUENCE),
    "other sender": (
        lambda data, _sequence: Reply(data, sender=1234),
        Rejection.MESSAGE_FROM_A_SENDER_OTHER_THAN_THE_KERNEL,
    ),
    "unexpected type": (
        lambda _data, sequence: Reply(
            message(RTM_NEWADDR, sequence, link_payload()) + done(sequence)
        ),
        Rejection.UNEXPECTED_MESSAGE_TYPE,
    ),
}


@pytest.mark.parametrize(
    ("alter", "reason"), REJECTIONS.values(), ids=REJECTIONS.keys()
)
def test_a_flawed_dump_rejects_the_whole_read_as_an_os_error(
    alter: Alter, reason: Rejection
) -> None:
    altered_script = first_dump_altered(alter)

    with pytest.raises(DumpRejectedError) as raised:
        read(altered_script)

    assert isinstance(raised.value, OSError)
    assert raised.value.reason == reason


def test_a_flawed_later_dump_rejects_the_whole_read() -> None:
    def script(request_type: int, family: int, sequence: int) -> list[Reply]:
        if request_type == RTM_GETROUTE and family == socket.AF_INET6:
            return [Reply(message(NLMSG_OVERRUN, sequence, b""))]
        return good_replies(request_type, family, sequence)

    with pytest.raises(DumpRejectedError):
        read(script)


def test_a_dump_without_an_end_rejects_the_read_when_the_socket_times_out() -> None:
    def script(request_type: int, family: int, sequence: int) -> list[Reply]:
        replies = good_replies(request_type, family, sequence)
        return [replace(replies[0], data=replies[0].data[:-20])]

    with pytest.raises(DumpRejectedError) as raised:
        read(script)

    assert raised.value.reason == Rejection.NO_END_OF_DUMP_IN_TIME


def test_a_dump_that_outgrows_its_byte_bound_is_rejected() -> None:
    bounds = ReadBounds(max_bytes=40)

    with pytest.raises(DumpRejectedError) as raised:
        read(bounds=bounds)

    assert raised.value.reason == Rejection.DUMP_LARGER_THAN_ITS_BOUND


def test_a_dump_within_its_byte_bound_is_read() -> None:
    topology, _ = read(bounds=ReadBounds(max_bytes=10_000))

    assert topology.links


def test_a_read_that_outlasts_its_time_bound_is_rejected() -> None:
    ticks = iter(range(100))
    bounds = ReadBounds(max_seconds=3)

    with pytest.raises(DumpRejectedError) as raised:
        read(bounds=bounds, clock=lambda: float(next(ticks)))

    assert raised.value.reason == Rejection.NO_END_OF_DUMP_IN_TIME


DECODING_REJECTIONS = {
    "address without a value": (
        RTM_GETADDR,
        RTM_NEWADDR,
        struct.pack("=BBBBI", 2, 8, 0, 0, 1),
        Rejection.ADDRESS_WITHOUT_A_VALUE,
    ),
    "address of odd length": (
        RTM_GETADDR,
        RTM_NEWADDR,
        address_payload(2, 8, bytes(3)),
        Rejection.MALFORMED_ADDRESS,
    ),
    "output link of odd length": (
        RTM_GETROUTE,
        RTM_NEWROUTE,
        route_payload(2, 0, attribute(RTA_OIF, bytes(2))),
        Rejection.MALFORMED_INTEGER_ATTRIBUTE,
    ),
}


@pytest.mark.parametrize(
    ("request_type", "answer_type", "payload", "reason"),
    DECODING_REJECTIONS.values(),
    ids=DECODING_REJECTIONS.keys(),
)
def test_a_fact_that_cannot_be_decoded_rejects_the_whole_read(
    request_type: int, answer_type: int, payload: bytes, reason: Rejection
) -> None:
    def script(asked: int, family: int, sequence: int) -> list[Reply]:
        if asked == request_type and family == socket.AF_INET:
            return [Reply(message(answer_type, sequence, payload) + done(sequence))]
        return good_replies(asked, family, sequence)

    with pytest.raises(DumpRejectedError) as raised:
        read(script)

    assert raised.value.reason == reason


def test_the_real_kernel_reports_its_loopback_link() -> None:
    topology = read_topology()

    assert "lo" in {link.name for link in topology.links}
