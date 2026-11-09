"""Reads the kernel's links, addresses and routes over one NETLINK_ROUTE socket.

The reader returns facts and judges nothing. A dump counts only when it is
complete; every rejection raises `DumpRejectedError`, an `OSError`, so a caller that
names unreadable facts on `OSError` names these too.
"""

import ipaddress
import itertools
import socket
import struct
import time
from abc import abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

NLM_F_REQUEST = 0x1
NLM_F_DUMP = 0x300
NLM_F_DUMP_INTR = 0x10
NLMSG_ERROR = 2
NLMSG_DONE = 3
NLMSG_OVERRUN = 4
RTM_NEWLINK = 16
RTM_GETLINK = 18
RTM_NEWADDR = 20
RTM_GETADDR = 22
RTM_NEWROUTE = 24
RTM_GETROUTE = 26
IFF_LOOPBACK = 0x8
IFLA_IFNAME = 3
IFA_ADDRESS = 1
IFA_LOCAL = 2
RTA_OIF = 4
RTA_GATEWAY = 5
RTA_MULTIPATH = 9
RTA_TABLE = 15
RTA_VIA = 18
RTA_NH_ID = 30
NEXT_HOP_ATTRIBUTES = frozenset({RTA_GATEWAY, RTA_VIA, RTA_MULTIPATH, RTA_NH_ID})
ATTRIBUTE_TYPE_MASK = 0x3FFF
KERNEL_PORT = 0

HEADER = struct.Struct("=IHHII")
ATTRIBUTE_HEADER = struct.Struct("=HH")
LINK_BODY = struct.Struct("=BxHiII")
ADDRESS_BODY = struct.Struct("=BBBBI")
ROUTE_BODY = struct.Struct("=BBBBBBBBI")
STATUS = struct.Struct("=i")
INTEGER = struct.Struct("=I")
ALIGNMENT = 4
RECEIVE_SIZE = 65536


class Rejection(StrEnum):
    """Why a read was refused."""

    SHORT_NETLINK_HEADER = "short netlink header"
    SHORT_NETLINK_MESSAGE = "short netlink message"
    DUMP_INTERRUPTED = "dump interrupted"
    MESSAGE_OF_ANOTHER_SEQUENCE = "message of another sequence"
    SHORT_ATTRIBUTE_HEADER = "short attribute header"
    MALFORMED_ATTRIBUTE = "malformed attribute"
    SHORT_MESSAGE_BODY = "short message body"
    MALFORMED_INTEGER_ATTRIBUTE = "malformed integer attribute"
    LINK_WITHOUT_A_NAME = "link without a name"
    ADDRESS_WITHOUT_A_VALUE = "address without a value"
    MALFORMED_ADDRESS = "malformed address"
    NO_END_OF_DUMP_IN_TIME = "no end of dump in time"
    TRUNCATED_RECEIVE = "truncated receive"
    MESSAGE_FROM_A_SENDER_OTHER_THAN_THE_KERNEL = (
        "message from a sender other than the kernel"
    )
    DUMP_LARGER_THAN_ITS_BOUND = "dump larger than its bound"
    DUMP_ENDED_UNSUCCESSFULLY = "dump ended unsuccessfully"
    KERNEL_REPORTED_AN_ERROR = "kernel reported an error"
    DUMP_OVERRUN = "dump overrun"
    UNEXPECTED_MESSAGE_TYPE = "unexpected message type"


class DumpRejectedError(OSError):
    """The kernel's answer is not a complete, well-formed dump."""

    def __init__(self, reason: Rejection) -> None:  # noqa: D107 - the class names it
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Link:
    """A network interface."""

    index: int
    name: str
    flags: int


@dataclass(frozen=True)
class Address:
    """An address assigned to a link."""

    family: int
    prefix_length: int
    link_index: int
    ip_address: ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class Route:
    """A route; `has_next_hop` is true when any attribute names a next hop."""

    family: int
    table: int
    destination_prefix_length: int
    output_link_index: int | None
    has_next_hop: bool


@dataclass(frozen=True)
class Topology:
    """Everything one read found."""

    links: tuple[Link, ...]
    addresses: tuple[Address, ...]
    routes: tuple[Route, ...]


@dataclass(frozen=True)
class ReadBounds:
    """How much and how long one read may take."""

    max_bytes: int = 4 * 1024 * 1024
    max_seconds: float = 5.0


class NetlinkSocket(Protocol):
    """The socket calls the reader makes, so a test can stand in for the kernel."""

    @abstractmethod
    def settimeout(self, value: float | None, /) -> None:
        """Bound the next receive."""

    @abstractmethod
    def send(self, data: bytes, /) -> int:
        """Send one request datagram."""

    @abstractmethod
    def recvmsg(
        self, _buffer_size: int, /
    ) -> tuple[bytes, list[tuple[int, int, bytes]], int, tuple[int, int]]:
        """Receive one datagram with its flags and sender."""

    @abstractmethod
    def close(self) -> None:
        """Release the socket."""


def open_kernel_socket() -> NetlinkSocket:
    """Open a NETLINK_ROUTE socket; a dump needs no capability."""
    kernel_socket = socket.socket(
        socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE
    )
    kernel_socket.bind((0, 0))
    return kernel_socket


def _aligned(length: int) -> int:
    return (length + ALIGNMENT - 1) & ~(ALIGNMENT - 1)


def _request(message_type: int, sequence: int, body: bytes) -> bytes:
    length = HEADER.size + len(body)
    header = HEADER.pack(length, message_type, NLM_F_REQUEST | NLM_F_DUMP, sequence, 0)
    return header + body


def _parse_messages(data: bytes, sequence: int) -> Iterator[tuple[int, int, bytes]]:
    """Yield (type, flags, payload) of each message in one datagram, or reject it."""
    offset = 0
    while offset < len(data):
        if len(data) - offset < HEADER.size:
            raise DumpRejectedError(Rejection.SHORT_NETLINK_HEADER)
        length, message_type, flags, message_sequence, _ = HEADER.unpack_from(
            data, offset
        )
        if length < HEADER.size or offset + length > len(data):
            raise DumpRejectedError(Rejection.SHORT_NETLINK_MESSAGE)
        if flags & NLM_F_DUMP_INTR:
            raise DumpRejectedError(Rejection.DUMP_INTERRUPTED)
        if message_sequence != sequence:
            raise DumpRejectedError(Rejection.MESSAGE_OF_ANOTHER_SEQUENCE)
        yield message_type, flags, data[offset + HEADER.size : offset + length]
        offset += _aligned(length)


def _parse_attributes(data: bytes) -> dict[int, bytes]:
    attributes: dict[int, bytes] = {}
    offset = 0
    while offset < len(data):
        if len(data) - offset < ATTRIBUTE_HEADER.size:
            raise DumpRejectedError(Rejection.SHORT_ATTRIBUTE_HEADER)
        length, attribute_type = ATTRIBUTE_HEADER.unpack_from(data, offset)
        if length < ATTRIBUTE_HEADER.size or offset + length > len(data):
            raise DumpRejectedError(Rejection.MALFORMED_ATTRIBUTE)
        attributes[attribute_type & ATTRIBUTE_TYPE_MASK] = data[
            offset + ATTRIBUTE_HEADER.size : offset + length
        ]
        offset += _aligned(length)
    return attributes


def _split(layout: struct.Struct, payload: bytes) -> tuple[tuple[int, ...], bytes]:
    if len(payload) < layout.size:
        raise DumpRejectedError(Rejection.SHORT_MESSAGE_BODY)
    return layout.unpack_from(payload), payload[_aligned(layout.size) :]


def _read_integer(raw: bytes) -> int:
    if len(raw) != INTEGER.size:
        raise DumpRejectedError(Rejection.MALFORMED_INTEGER_ATTRIBUTE)
    return INTEGER.unpack(raw)[0]


def _decode_link(payload: bytes) -> Link:
    (_, _, index, flags, _), rest = _split(LINK_BODY, payload)
    name = _parse_attributes(rest).get(IFLA_IFNAME)
    if name is None:
        raise DumpRejectedError(Rejection.LINK_WITHOUT_A_NAME)
    return Link(index, name.rstrip(b"\0").decode(errors="replace"), flags)


def _decode_address(payload: bytes) -> Address:
    (family, prefix_length, _, _, link_index), rest = _split(ADDRESS_BODY, payload)
    attributes = _parse_attributes(rest)
    raw = attributes.get(IFA_LOCAL, attributes.get(IFA_ADDRESS))
    if raw is None:
        raise DumpRejectedError(Rejection.ADDRESS_WITHOUT_A_VALUE)
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as error:
        raise DumpRejectedError(Rejection.MALFORMED_ADDRESS) from error
    return Address(family, prefix_length, link_index, address)


def _decode_route(payload: bytes) -> Route:
    (family, destination_length, _, _, table, *_), rest = _split(ROUTE_BODY, payload)
    attributes = _parse_attributes(rest)
    if RTA_TABLE in attributes:
        table = _read_integer(attributes[RTA_TABLE])
    output = attributes.get(RTA_OIF)
    return Route(
        family,
        table,
        destination_length,
        None if output is None else _read_integer(output),
        bool(NEXT_HOP_ATTRIBUTES & attributes.keys()),
    )


class _Budget:
    def __init__(self, bounds: ReadBounds, clock: Callable[[], float]) -> None:
        self._bounds = bounds
        self._clock = clock
        self._deadline = clock() + bounds.max_seconds
        self._received = 0

    def receive(self, kernel_socket: NetlinkSocket) -> bytes:
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            raise DumpRejectedError(Rejection.NO_END_OF_DUMP_IN_TIME)
        kernel_socket.settimeout(remaining)
        try:
            data, _, flags, (sender, _) = kernel_socket.recvmsg(RECEIVE_SIZE)
        except TimeoutError as error:
            raise DumpRejectedError(Rejection.NO_END_OF_DUMP_IN_TIME) from error
        if flags & socket.MSG_TRUNC:
            raise DumpRejectedError(Rejection.TRUNCATED_RECEIVE)
        if sender != KERNEL_PORT:
            raise DumpRejectedError(
                Rejection.MESSAGE_FROM_A_SENDER_OTHER_THAN_THE_KERNEL
            )
        self._received += len(data)
        if self._received > self._bounds.max_bytes:
            raise DumpRejectedError(Rejection.DUMP_LARGER_THAN_ITS_BOUND)
        return data


def _reject_unless_answer(message_type: int, answer_type: int) -> None:
    if message_type == NLMSG_ERROR:
        raise DumpRejectedError(Rejection.KERNEL_REPORTED_AN_ERROR)
    if message_type == NLMSG_OVERRUN:
        raise DumpRejectedError(Rejection.DUMP_OVERRUN)
    if message_type != answer_type:
        raise DumpRejectedError(Rejection.UNEXPECTED_MESSAGE_TYPE)


def _collect_payloads(
    kernel_socket: NetlinkSocket, budget: _Budget, sequence: int, answer_type: int
) -> list[bytes]:
    payloads: list[bytes] = []
    while True:
        data = budget.receive(kernel_socket)
        for message_type, _, payload in _parse_messages(data, sequence):
            if message_type == NLMSG_DONE:
                if len(payload) < STATUS.size or STATUS.unpack_from(payload)[0] != 0:
                    raise DumpRejectedError(Rejection.DUMP_ENDED_UNSUCCESSFULLY)
                return payloads
            _reject_unless_answer(message_type, answer_type)
            payloads.append(payload)


def read_topology(
    open_socket: Callable[[], NetlinkSocket] = open_kernel_socket,
    bounds: ReadBounds = ReadBounds(),  # noqa: B008 - a frozen value
    clock: Callable[[], float] = time.monotonic,
) -> Topology:
    """Read the links, the addresses and the routes of both families, or reject."""
    kernel_socket = open_socket()
    sequences = itertools.count(1)
    budget = _Budget(bounds, clock)

    def dump(request_type: int, answer_type: int, body: bytes) -> list[bytes]:
        sequence = next(sequences)
        kernel_socket.send(_request(request_type, sequence, body))
        return _collect_payloads(kernel_socket, budget, sequence, answer_type)

    families = (socket.AF_INET, socket.AF_INET6)
    try:
        links = dump(RTM_GETLINK, RTM_NEWLINK, bytes(LINK_BODY.size))
        addresses = [
            payload
            for family in families
            for payload in dump(
                RTM_GETADDR,
                RTM_NEWADDR,
                bytes([family]).ljust(ADDRESS_BODY.size, b"\0"),
            )
        ]
        routes = [
            payload
            for family in families
            for payload in dump(
                RTM_GETROUTE,
                RTM_NEWROUTE,
                bytes([family]).ljust(ROUTE_BODY.size, b"\0"),
            )
        ]
    finally:
        kernel_socket.close()
    return Topology(
        tuple(map(_decode_link, links)),
        tuple(map(_decode_address, addresses)),
        tuple(map(_decode_route, routes)),
    )
