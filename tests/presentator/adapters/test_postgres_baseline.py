"""What the migrated database refuses, and what it fills in on its own."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from tests.support.postgres import engine_on

_A_USER = "INSERT INTO users (id, username, role) VALUES ('u1', 'ada', 'admin')"


@pytest.fixture
def connection(database_url: str) -> Iterator[Connection]:
    engine = engine_on(database_url)
    try:
        with engine.begin() as open_connection:
            yield open_connection
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "refused",
    [
        pytest.param(
            "INSERT INTO users (id, username, role, kind)"
            " VALUES ('u2', 'bo', 'x', 'robot')",
            id="a kind that is neither a person nor an agent",
        ),
        pytest.param(
            "INSERT INTO instance_settings (id, name) VALUES (2, 'second')",
            id="a second row of instance settings",
        ),
        pytest.param(
            "INSERT INTO sessions (id, user_id, created_at, expires_at)"
            " VALUES ('s1', 'nobody', now(), now())",
            id="a session of a user that does not exist",
        ),
        pytest.param(
            "INSERT INTO host_key_pins (host, public_key)"
            " VALUES ('git.example', 'ssh-ed25519 A'), ('git.example', 'ssh-ed25519 B')",
            id="a second key pinned for one host",
        ),
    ],
)
def test_a_migrated_database_refuses(connection: Connection, refused: str) -> None:
    connection.execute(text(_A_USER))

    with pytest.raises(IntegrityError):
        connection.execute(text(refused))


def test_a_user_without_a_kind_is_a_person(connection: Connection) -> None:
    connection.execute(text(_A_USER))

    kind = connection.execute(text("SELECT kind FROM users")).scalar_one()

    assert kind == "person"


def test_a_run_of_a_source_gets_the_next_number(connection: Connection) -> None:
    connection.execute(text(_A_USER))
    connection.execute(
        text(
            "INSERT INTO sources (id, name, url, ref, encrypted_secret, owner_id)"
            " VALUES ('src', 'n', 'u', 'main', ''::bytea, 'u1')"
        )
    )
    run = "INSERT INTO source_runs (source_id, at, outcome) VALUES ('src', now(), 'ok')"

    connection.execute(text(run))
    connection.execute(text(run))

    numbers = connection.execute(text("SELECT id FROM source_runs ORDER BY id"))
    assert [number for (number,) in numbers] == [1, 2]
