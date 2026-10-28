"""Constants and cleanup helpers for the tests that spawn gated children."""

from presentator.adapters.worker_children import GatedChild

WAIT_SECONDS = 10
PATH = "/usr/bin:/bin"


def release_and_close(child: GatedChild) -> None:
    try:
        child.release()
    finally:
        child.close()


def end_child(child: GatedChild) -> int:
    try:
        child.kill()
    finally:
        reaped_status = child.wait()
    return reaped_status
