"""The build worker's start-up boundary made of fakes, and the settings it loads."""

from collections.abc import Callable

from presentator.host.config import WorkerSettings
from presentator.host.worker import Boundary
from presentator.host.worker_self_check import Seal, Snapshot

LOADED_SETTINGS = WorkerSettings(build_timeout_seconds=45.0, build_output_megabytes=7)


def boundary_over(
    snapshot: Snapshot,
    events: list[str],
    served: dict[str, object] | None = None,
    load_settings: Callable[[], WorkerSettings] = lambda: LOADED_SETTINGS,
    seal: Seal = Seal.COMPOSE,
) -> Boundary:
    """A boundary whose reads give `seal` and `snapshot`, recording the steps run."""

    def read_snapshot(_seal: Seal) -> Snapshot:
        events.append("read")
        return snapshot

    def settings() -> WorkerSettings:
        events.append("settings")
        return load_settings()

    def serve(**arguments: object) -> int:
        events.append("serve")
        if served is not None:
            served.update(arguments)
        return 0

    return Boundary(
        set_non_dumpable=lambda: events.append("prctl"),
        set_umask=lambda: events.append("umask"),
        read_seal=lambda: seal,
        read_snapshot=read_snapshot,
        load_settings=settings,
        serve=serve,
    )
