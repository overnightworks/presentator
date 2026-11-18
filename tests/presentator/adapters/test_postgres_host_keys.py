"""The ssh host keys every process trusts, pinned in Postgres."""

from sqlalchemy import Engine

from gitmirror.model import HostKey
from presentator.adapters.postgres.host_keys import PostgresHostKeyPins

_THE_KEY = HostKey(host="git.example", key="ssh-ed25519 AAAAthe-first-key")
_A_CHANGED_KEY = HostKey(host="git.example", key="ssh-ed25519 AAAAa-changed-key")


def test_a_second_key_pinned_for_a_host_is_answered_with_the_first(
    engine: Engine,
) -> None:
    pins = PostgresHostKeyPins(engine=engine)
    pins.pin(_THE_KEY)

    standing = pins.pin(_A_CHANGED_KEY)

    assert standing == _THE_KEY
    assert pins.pinned() == (_THE_KEY,)
