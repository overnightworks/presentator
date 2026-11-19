"""How this web builds a deck: the build bound, the runner, the toolchain identity."""

from datetime import timedelta

from presentator.adapters.build_worker import WorkerToolchain
from presentator.adapters.builds import DeckToolchain, HostToolchain
from presentator.adapters.toolchain_identity import toolchain_identity_of
from presentator.contracts.decks import ToolchainIdentity
from presentator.host.config import Settings, WhereBuildsRun
from presentator.host.environment import cannot_start, variable_of
from presentator.host.worker_serving import emergency_end


def identity_of_the_toolchain(settings: Settings) -> ToolchainIdentity:
    """The identity of this web's own toolchain project, or a refusal to start.

    Computed once, for the worker runner and the host runner alike: both build
    from the project this instance carries.
    """
    try:
        return toolchain_identity_of(settings.toolchain)
    except OSError as unreadable:
        setting = variable_of("toolchain")
        reason = f"{setting}: the project cannot be read: {unreadable}"
        raise cannot_start(reason, variables=(setting,)) from None


def what_builds_a_deck(settings: Settings, *, bound: timedelta) -> DeckToolchain:
    """The sealed build worker, or this machine's own toolchain project.

    A deck is code (line 14a), so a build runs in the worker unless this
    deployment asked in as many words for the development run.
    """
    if settings.build_runner is WhereBuildsRun.HOST:
        return HostToolchain(project=settings.toolchain, bound=bound)
    return the_build_worker(settings, bound=bound)


def build_bound_of(settings: Settings) -> timedelta:
    """The one bound of a build step, shared by every runner and the probe."""
    return timedelta(seconds=settings.build_timeout_seconds)


def the_build_worker(settings: Settings, *, bound: timedelta) -> WorkerToolchain:
    """The client of the sealed build workers; no worker has to answer to start."""
    link = settings.build_worker
    return WorkerToolchain(
        host=link.host,
        port=link.port,
        key=link.key.get_secret_value().encode(),
        megabytes=settings.build_output_megabytes,
        reply_bound_seconds=emergency_end(0.0, bound.total_seconds()),
    )
