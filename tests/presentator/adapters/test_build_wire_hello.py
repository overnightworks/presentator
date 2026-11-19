"""The keyed hello: only a peer that holds the key and the direction is verified."""

import socket
import threading
from collections.abc import Callable

import pytest

from presentator.adapters.build_wire import (
    CHALLENGE_BYTES,
    HELLO_VERSION,
    NONCE_BYTES,
    TAG_BYTES,
    HelloDirection,
    HelloFault,
    HelloRefusedError,
    answer_hello,
    hello_tag,
    verify_hello,
)
from tests.support.worker_support import KEY

SECONDS = 0.5
OTHER_KEY = b"x" * len(KEY)
WEB_NONCE = b"w" * NONCE_BYTES


def run_worker_side(
    connection: socket.socket, key: bytes, verdicts: list[HelloFault | None]
) -> threading.Thread:
    def verify() -> None:
        try:
            verify_hello(connection, key, seconds=SECONDS)
        except HelloRefusedError as refused:
            verdicts.append(refused.fault)
            connection.close()
        else:
            verdicts.append(None)

    thread = threading.Thread(target=verify)
    thread.start()
    return thread


def web_answering_as(
    direction: HelloDirection, key: bytes, version: int = HELLO_VERSION
) -> Callable[[socket.socket], None]:
    def answer(connection: socket.socket) -> None:
        challenge = connection.recv(CHALLENGE_BYTES, socket.MSG_WAITALL)
        tag = hello_tag(key, direction, challenge[1:], WEB_NONCE)
        connection.sendall(bytes([version]) + WEB_NONCE + tag)

    return answer


def test_a_web_with_the_key_is_verified_and_verifies_the_worker(
    wire: tuple[socket.socket, socket.socket],
) -> None:
    worker, web = wire
    verdicts: list[HelloFault | None] = []

    thread = run_worker_side(worker, KEY, verdicts)
    answer_hello(web, KEY)
    thread.join()

    assert verdicts == [None]
    assert worker.gettimeout() is None


def no_answer(connection: socket.socket) -> None:
    connection.recv(CHALLENGE_BYTES)


def closed_after_the_challenge(connection: socket.socket) -> None:
    connection.recv(CHALLENGE_BYTES)
    connection.close()


REFUSED: dict[str, tuple[Callable[[socket.socket], None], HelloFault]] = {
    "another key": (
        web_answering_as(HelloDirection.WEB_TO_WORKER, OTHER_KEY),
        HelloFault.KEY,
    ),
    "the reflected direction": (
        web_answering_as(HelloDirection.WORKER_TO_WEB, KEY),
        HelloFault.KEY,
    ),
    "another version": (
        web_answering_as(HelloDirection.WEB_TO_WORKER, KEY, version=HELLO_VERSION + 1),
        HelloFault.VERSION,
    ),
    "no answer": (no_answer, HelloFault.TIMEOUT),
    "closed after the challenge": (closed_after_the_challenge, HelloFault.CLOSE),
}


@pytest.mark.parametrize(("answer", "fault"), REFUSED.values(), ids=REFUSED.keys())
def test_a_web_that_does_not_prove_the_key_and_direction_is_refused_by_its_fault(
    wire: tuple[socket.socket, socket.socket],
    answer: Callable[[socket.socket], None],
    fault: HelloFault,
) -> None:
    worker, web = wire
    verdicts: list[HelloFault | None] = []

    thread = run_worker_side(worker, KEY, verdicts)
    answer(web)
    thread.join()

    assert verdicts == [fault]


def test_a_worker_that_does_not_prove_the_key_is_refused_by_the_web(
    wire: tuple[socket.socket, socket.socket],
) -> None:
    worker, web = wire
    verdicts: list[HelloFault | None] = []

    thread = run_worker_side(worker, OTHER_KEY, verdicts)
    with pytest.raises(HelloRefusedError):
        answer_hello(web, KEY)
    thread.join()

    assert verdicts == [HelloFault.KEY]


def test_a_worker_that_proves_with_a_wrong_tag_is_refused_by_the_web(
    wire: tuple[socket.socket, socket.socket],
) -> None:
    worker, web = wire
    worker.sendall(bytes([HELLO_VERSION]) + b"n" * NONCE_BYTES)
    worker.sendall(b"t" * TAG_BYTES)

    with pytest.raises(HelloRefusedError):
        answer_hello(web, KEY)


def test_a_hello_given_no_time_is_refused_as_a_timeout(
    wire: tuple[socket.socket, socket.socket],
) -> None:
    worker, _web = wire

    with pytest.raises(HelloRefusedError) as refused:
        verify_hello(worker, KEY, seconds=0)

    assert refused.value.fault == HelloFault.TIMEOUT


def test_a_worker_of_another_version_is_refused_by_the_web(
    wire: tuple[socket.socket, socket.socket],
) -> None:
    worker, web = wire
    worker.sendall(bytes([HELLO_VERSION + 1]) + b"n" * NONCE_BYTES)

    with pytest.raises(HelloRefusedError):
        answer_hello(web, KEY)


def test_a_tag_of_one_direction_is_not_the_tag_of_the_other() -> None:
    nonce = b"n" * NONCE_BYTES
    forward = hello_tag(KEY, HelloDirection.WEB_TO_WORKER, nonce, WEB_NONCE)

    assert forward != hello_tag(KEY, HelloDirection.WORKER_TO_WEB, nonce, WEB_NONCE)
