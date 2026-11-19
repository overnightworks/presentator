"""Where the account events of a sign-in and a back-channel logout are written."""

import logging

from webauth.ports import AccountEvent

_log = logging.getLogger(__name__)


class LoggingAuditSink:
    """Account events as log lines: the kind, the person (`sub`) and the roles.

    The event carries no username and no token, so neither can reach the log.
    """

    def account_event(self, event: AccountEvent) -> None:
        """Write one `account_event.recorded` line per event at INFO."""
        _log.info(
            "account_event.recorded",
            extra={
                "kind": event.kind.value,
                "sub": event.subject_id,
                "role": event.role,
                "previous_role": event.previous_role,
            },
        )
