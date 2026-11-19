"""How the relabelled web pod's connections to a worker's build port end in the fake."""

from collections.abc import Sequence

from tests.scripts.support.k3d_pods import Issued

CONNECTED = "connected"
REFUSED = "ECONNREFUSED"


def reach_positions(commands: Sequence[Issued], host: str) -> list[int]:
    """Where the commands send the reach program into a pod, aimed at the host."""
    return [
        at
        for at, (command, _, _) in enumerate(commands)
        if command[2:4] == ("kubectl", "exec")
        and command[-2] == "reach"
        and command[-1].rpartition(":")[0] == host
    ]


def policy_position(commands: Sequence[Issued]) -> int | None:
    """Where the commands first apply the probe's policy, if they do."""
    return next(
        (
            at
            for at, (command, _, stdin) in enumerate(commands)
            if command[:2] == ("kubectl", "apply") and "probe-inbound" in (stdin or "")
        ),
        None,
    )


def witness_end(
    commands: Sequence[Issued],
    host: str,
    *,
    connects_before_the_refusal: int | None,
    refusals_before_the_admission: int | None,
) -> str:
    """The cluster refuses the witness once relabelled, and admits it by the policy.

    A knob that is None means the cluster never does that: the witness connects
    for ever before the refusal, and is refused for ever before the admission.
    """
    reaches = reach_positions(commands, host)
    applied = policy_position(commands)
    if applied is not None and refusals_before_the_admission is not None:
        since = [at for at in reaches if at > applied]
        if len(since) > refusals_before_the_admission:
            return CONNECTED
    if connects_before_the_refusal is None:
        return CONNECTED
    return REFUSED if len(reaches) > connects_before_the_refusal else CONNECTED
