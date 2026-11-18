"""A start that cannot listen ends in logline's one coded bind line."""

import json
import socket
from contextlib import closing
from importlib.resources import files

import pytest
from uvicorn.config import STARTUP_FAILURE

from presentator.host import main
from tests.support.log_lines import LoggedText, events_of

_LOOPBACK = "127.0.0.1"
# An address of the documentation range, which no host of the tests holds.
_AN_ADDRESS_NOT_HERE = "192.0.2.1"


def _base_keys() -> frozenset[str]:
    """The keys logline writes on every line, read from its published standard."""
    standard = json.loads((files("logline") / "standard.json").read_text())
    return frozenset(standard["base_keys"])


@pytest.mark.usefixtures("served_database")
@pytest.mark.parametrize(
    ("host", "reason"),
    [(_LOOPBACK, "port_taken"), (_AN_ADDRESS_NOT_HERE, "os_error")],
)
def test_a_start_that_cannot_listen_ends_in_one_coded_bind_line_and_no_ready_line(
    environment: pytest.MonkeyPatch, logged: LoggedText, host: str, reason: str
) -> None:
    with closing(socket.socket()) as holder:
        holder.bind((_LOOPBACK, 0))
        holder.listen()
        port = str(holder.getsockname()[1])
        environment.setenv("PRESENTATOR_HOST", host)
        environment.setenv("PRESENTATOR_PORT", port)

        with pytest.raises(SystemExit) as ended:
            main.main([])

    assert ended.value.code == STARTUP_FAILURE
    (failed,) = events_of(logged, "server_bind.failed")
    codes = {key: value for key, value in failed.items() if key not in _base_keys()}
    assert codes == {"reason": reason, "error_type": OSError.__name__}
    assert failed["level"] == "error"
    assert not events_of(logged, "process.ready")
    assert host not in logged.text
    assert f":{port}" not in logged.text
