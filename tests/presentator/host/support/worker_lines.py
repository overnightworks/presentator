"""What the worker's process lines say, read from the JSON lines it wrote."""

from presentator.host.lifecycle import ServerEvent
from tests.support.log_lines import LoggedText, events_of


def refusal_reasons(logged: LoggedText) -> list[str]:
    """The `reason` of every `process.failed` line written so far."""
    return [
        str(line["reason"]) for line in events_of(logged, ServerEvent.PROCESS_FAILED)
    ]
