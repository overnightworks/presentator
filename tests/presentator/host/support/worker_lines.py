"""What the worker's process lines say, read from the JSON lines it wrote."""

from tests.support.log_lines import LoggedText, events_of


def refusal_reasons(logged: LoggedText) -> list[str]:
    """The `reason` of every `start.refused` line written so far."""
    return [str(line["reason"]) for line in events_of(logged, "start.refused")]
