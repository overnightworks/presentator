"""The residue planter refuses to start the worker when its plant failed."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

PLANTER = Path(__file__).parents[2] / "scripts/probe_build_worker/plant_residue.py"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can always change identity")
def test_a_plant_that_cannot_change_identity_exits_1_without_running_the_command(
    tmp_path: Path,
) -> None:
    ran = tmp_path / "ran"
    planted = subprocess.run(  # noqa: S603
        (sys.executable, "-I", "-", "process", "1:1", "touch", str(ran)),
        input=PLANTER.read_text(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert planted.returncode == 1
    assert not ran.exists()
