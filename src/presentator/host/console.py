"""The two console scripts, which a SIGINT ends silently until they can log.

Python turns a SIGINT into a KeyboardInterrupt from its start, so one that
lands while a script imports its program ends in a traceback. Each script first
gives SIGINT back its default action, which ends the process as the signal
does and writes nothing, and only then imports its program; once logging is
configured, `signals_end_as_a_stop` takes the stop signals over and hands them
back at its end. This module imports nothing else, so the window before the
script owns SIGINT is the import of its two parent packages' docstrings.
"""

import signal


def _interrupt_at_its_default_action() -> None:
    signal.signal(signal.SIGINT, signal.SIG_DFL)


def presentator() -> None:
    """Run the `presentator` command."""
    _interrupt_at_its_default_action()
    from presentator.host.main import main  # noqa: PLC0415 - imported once SIGINT is silent

    main()


def presentator_worker() -> int:
    """Run the `presentator-worker` command; its exit status."""
    _interrupt_at_its_default_action()
    from presentator.host.worker import main  # noqa: PLC0415 - imported once SIGINT is silent

    return main()
