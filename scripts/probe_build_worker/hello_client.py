"""Show that a worker refuses a hello it cannot verify and still serves a true one.

Usage: `python -I -`, the program on stdin.

Run inside the web's container as its own user. It reads the worker's address
and the key through `load_settings()` and opens three connections to the
Service, each of which gets the worker's challenge. The first answers under a
key it does not hold, the second answers under the right key but the other
direction's label, which is the hello a worker reflected back would carry, and
the third answers truly. It prints one JSON line naming, for each, whether the
worker proved itself to the end of the hello.
"""

import json
import os
import socket
import sys

from presentator.adapters.build_wire import (
    CHALLENGE_BYTES,
    HELLO_VERSION,
    NONCE_BYTES,
    TAG_BYTES,
    HelloDirection,
    hello_tag,
)
from presentator.host.config import load_settings

SECONDS = 20.0
WRONG_KEY = b"x" * 32


def proven(host: str, port: int, key: bytes, direction: HelloDirection) -> bool:
    """Whether the worker proves itself to an answer under the key and label."""
    with socket.create_connection((host, port), timeout=SECONDS) as connection:
        challenge = connection.recv(CHALLENGE_BYTES, socket.MSG_WAITALL)
        nonce, web_nonce = challenge[1:], bytes(NONCE_BYTES)
        tag = hello_tag(key, direction, nonce, web_nonce)
        connection.sendall(bytes([HELLO_VERSION]) + web_nonce + tag)
        try:
            return len(connection.recv(TAG_BYTES, socket.MSG_WAITALL)) == TAG_BYTES
        except ConnectionResetError:
            return False


def main() -> None:
    """Run the three hellos and print what the worker did with each."""
    # The probe only reads the build worker's link and never reaches Redis; the
    # pod's manifest names the URL once platform runs one, and then this line goes.
    os.environ.setdefault("PRESENTATOR_REDIS_URL", "redis://127.0.0.1:6379/0")
    link = load_settings().build_worker
    key = link.key.get_secret_value().encode()
    answers = {
        "wrong key": (WRONG_KEY, HelloDirection.WEB_TO_WORKER),
        "reflected": (key, HelloDirection.WORKER_TO_WEB),
        "true": (key, HelloDirection.WEB_TO_WORKER),
    }
    said = {
        name: proven(link.host, link.port, answered_key, direction)
        for name, (answered_key, direction) in answers.items()
    }
    sys.stdout.write(json.dumps(said) + "\n")


if __name__ == "__main__":
    main()
