"""What the web probe's client prints, built from the probe's own outcome types."""

import json
from types import ModuleType
from typing import Any


def outage_outcome(web_module: ModuleType) -> Any:  # noqa: ANN401 - the web probe's Outcome, loaded by name
    """The outcome of a client whose first step found no worker."""
    step = web_module.StepOutcome("build", failed=True, failure=None, admitted=None)
    return web_module.Outcome(steps=(step,), out=(), talk_page=False, pdf=False)


def delivered_outcome(
    web_module: ModuleType, *, first_step_at: float, second_step_at: float
) -> Any:  # noqa: ANN401 - the web probe's Outcome, loaded by name
    """The outcome of a client whose build and export delivered the talk and PDF."""
    steps = tuple(
        web_module.StepOutcome(step, failed=False, failure=None, admitted=admitted)
        for step, admitted in zip(
            web_module.HELLO_STEPS, (first_step_at, second_step_at), strict=True
        )
    )
    return web_module.Outcome(
        steps=steps, out=("deck.pdf", "talk"), talk_page=True, pdf=True
    )


def client_output(outcome: Any) -> str:  # noqa: ANN401 - the web probe's Outcome, loaded by name
    """What the web's client prints: its outcome."""
    steps = [
        {
            "step": step.step,
            "failed": step.failed,
            "failure": step.failure,
            "admitted": step.admitted,
        }
        for step in outcome.steps
    ]
    said = {
        "steps": steps,
        "out": list(outcome.out),
        "talk_page": outcome.talk_page,
        "pdf": outcome.pdf,
    }
    return f"{json.dumps(said)}\n"
