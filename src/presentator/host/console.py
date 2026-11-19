"""The two console scripts, which a SIGINT ends silently until they can log.

Python turns a SIGINT into a KeyboardInterrupt from its start, so one that
lands while a script imports its program ends in a traceback. Each script first
gives SIGINT back its default action, which ends the process as the signal
does and writes nothing, and only then imports its program; once logging is
configured, `signals_end_as_a_stop` takes the stop signals over and hands them
back at its end. This module imports only the interpreter's own built-in
signal module, which its start has loaded already, so the window before the
script owns SIGINT is the import of its two parent packages' docstrings.
"""

# `signal` imports `enum`, and `typing` costs more: windows of milliseconds a
# SIGINT lands in. `_signal`, which `signal` wraps, has no stub of its own, so
# pyright reads it as `signal`; the scanners read this local flag as constant.
TYPE_CHECKING = False
if TYPE_CHECKING:  # NOSONAR(S5797, S2589)
    import signal as _signal
else:
    import _signal


def _interrupt_at_its_default_action() -> None:
    """Give SIGINT its default action, unless the parent process ignores it."""
    if _signal.getsignal(_signal.SIGINT) != _signal.SIG_IGN:
        _signal.signal(_signal.SIGINT, _signal.SIG_DFL)


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
