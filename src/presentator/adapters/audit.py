"""Where the account events of a sign-in and a back-channel logout are written."""

import logging
from dataclasses import dataclass, field

from webauth.ports import AccountEvent, AuditSink

_log = logging.getLogger(__name__)


class LoggingAuditSink:
    """Account events as log lines: the kind, the subject and the roles.

    The event carries no username and no token, so neither can reach the log.
    """

    def account_event(self, event: AccountEvent) -> None:
        """Write one line per event at INFO."""
        _log.info(
            "account event %s: subject %s, role %s, previous role %s",
            event.kind.value,
            event.subject_id,
            event.role,
            event.previous_role,
        )


@dataclass(slots=True)
class BufferedAudit:
    """Collects the events of one transaction, so a rolled-back one leaves no line."""

    events: list[AccountEvent] = field(default_factory=list[AccountEvent])

    def account_event(self, event: AccountEvent) -> None:
        """Hold the event until the transaction has committed."""
        self.events.append(event)

    def flush_to(self, sink: AuditSink) -> None:
        """Hand the held events to the sink, in the order they happened."""
        for event in self.events:
            sink.account_event(event)
