"""The residues the worker's start refusals are proved against: cells 61 to 64.

`probe_build_worker/plant_residue.py` runs first in a pod of its own, plants one
residue and replaces itself with the worker's command, so the worker is PID 1 of
a container that holds the residue. The refusals run the full start, and the
control passes `--check`.
"""

from probe_build_worker_harness import PROBE_SOURCES

from presentator.host.worker_self_check import Predicate

RESIDUE_SOURCE = PROBE_SOURCES / "plant_residue.py"
CHECK_ARGUMENT = "--check"
REFUSALS = (
    ("61", Predicate.ALONE, ("process",)),
    ("62", Predicate.SCRATCH, ("work-entry", "closed-directory", "shared-memory-file")),
    ("63", Predicate.SCRATCH, ("message-queue",)),
    ("64", Predicate.SCRATCH, ("sysv-shm", "sysv-msg", "sysv-sem")),
)
