"""The gated spawn the worker chain tests run real roles through, and its constants."""

import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from presentator.adapters.worker_children import (
    ChildSurroundings,
    GatedChild,
    spawn_gated,
)
from presentator.host.worker_serving import WireChild
from tests.support.step_support import OWN_IDENTITY
from tests.support.worker_support import assert_gone, process_id_of, role_of

WAIT_SECONDS = 20
EXITING_SEVEN = "import sys; sys.exit(7)"
EXITING_ZERO = "import sys; sys.exit(0)"
SLEEPER = "import time; time.sleep(120)"


@dataclass
class ChainSpawn:
    """The real gated spawn, with a program in place of a role where a test asks.

    The step exits 0 at once unless a test gives it a program or asks for the
    real launcher.
    """

    programs: Mapping[str, list[str]] = field(default_factory=dict[str, list[str]])
    real_launcher: bool = False
    sender_gate: Callable[[GatedChild], WireChild] = lambda child: child
    roles: list[str] = field(default_factory=list[str])
    children: list[GatedChild] = field(default_factory=list[GatedChild])
    process_ids: list[int] = field(default_factory=list[int])

    def __call__(
        self,
        command: Sequence[str],
        *,
        environment: Mapping[str, str],
        pass_fds: Sequence[int],
    ) -> WireChild:
        role = role_of(command)
        child = self._started(
            role,
            self._substituted(role, command, pass_fds),
            environment,
            pass_fds,
            ChildSurroundings(),
        )
        return self.sender_gate(child) if role == "send" else child

    def step(
        self,
        command: Sequence[str],
        *,
        environment: Mapping[str, str],
        pass_fds: Sequence[int],
        surroundings: ChildSurroundings,
    ) -> GatedChild:
        return self._started(
            "step",
            self._substituted("step", command, pass_fds),
            environment,
            pass_fds,
            surroundings,
        )

    def _substituted(
        self, role: str, command: Sequence[str], pass_fds: Sequence[int]
    ) -> Sequence[str]:
        program = self.programs.get(role)
        if program is None and role == "step" and not self.real_launcher:
            program = [EXITING_ZERO]
        if program is None:
            return command
        source, *extras = program
        return [sys.executable, "-I", "-c", source, *map(str, pass_fds), *extras]

    def _started(
        self,
        role: str,
        command: Sequence[str],
        environment: Mapping[str, str],
        pass_fds: Sequence[int],
        surroundings: ChildSurroundings,
    ) -> GatedChild:
        self.roles.append(role)
        child = spawn_gated(
            command,
            identity=OWN_IDENTITY,
            environment=environment,
            pass_fds=pass_fds,
            surroundings=surroundings,
        )
        self.children.append(child)
        self.process_ids.append(process_id_of(child))
        return child

    def kill_and_reap(self) -> None:
        for child in self.children:
            child.kill()
        unreaped = [
            child for child in self.children if child.wait_within(WAIT_SECONDS) is None
        ]
        assert not unreaped, "a killed child was not reaped"
        assert_gone(self.process_ids)


def await_absence(process_ids: list[int]) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline and any(
        Path(f"/proc/{process_id}").exists() for process_id in process_ids
    ):
        time.sleep(0.05)
    assert not any(Path(f"/proc/{process_id}").exists() for process_id in process_ids)
