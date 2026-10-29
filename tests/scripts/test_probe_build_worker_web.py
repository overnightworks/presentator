"""The web probe driver reads a finished client's run name and outcome."""

import subprocess
import sys
from collections.abc import Callable
from types import ModuleType


def test_finished_web_client_preserves_its_run_and_refusal_outcome(
    load_script: Callable[..., ModuleType],
) -> None:
    web = load_script("probe_build_worker_web")
    program = """
print('{"run": "refused-build"}')
print('{"steps": [{"step": "build", "failed": true, "text": "Build refused"}], '
      '"staged": [], "out": [], "talk_page": false, "pdf": false, '
      '"worklet": false, "worklet_sha256": null}')
"""
    with subprocess.Popen(  # noqa: S603
        (sys.executable, "-c", program),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as client:
        try:
            client.wait(timeout=10)
            assert web.run_name_of(client) == "refused-build"
            assert web.outcome_of(client) == web.Outcome(
                steps=(
                    web.StepOutcome(step="build", failed=True, text="Build refused"),
                ),
                staged=(),
                out=(),
                talk_page=False,
                pdf=False,
                worklet=False,
                worklet_sha256=None,
            )
        finally:
            if client.poll() is None:
                client.kill()
            client.wait(timeout=10)
