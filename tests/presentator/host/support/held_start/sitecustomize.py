"""Hold a console script at one point of its start until its standard input ends.

Python imports this module by its bare name before the script runs, once its
directory is on `PYTHONPATH`. The variable `HOLD_AT` names the module whose
import holds, or `AT_EXIT` for the end of the interpreter, after every other
exit handler. On reaching the hold it writes one byte to the descriptor that
`HELD_DESCRIPTOR` names, so the parent signals a process held there.
"""

import atexit
import os
import sys
from collections.abc import Sequence
from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import Final

HOLD_AT: Final = "PRESENTATOR_TEST_HOLD_AT"
HELD_DESCRIPTOR: Final = "PRESENTATOR_TEST_HELD_DESCRIPTOR"
AT_EXIT: Final = ":exit"
HELD: Final = b"\n"


def _hold() -> None:
    os.write(int(os.environ[HELD_DESCRIPTOR]), HELD)
    os.read(sys.stdin.fileno(), 1)


class _HoldBeforeImport:
    """A finder that finds nothing and holds once, as the named module is looked up."""

    def __init__(self, module: str) -> None:
        self._module = module

    def find_spec(
        self,
        name: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        del path, target
        if name == self._module:
            sys.meta_path.remove(self)
            _hold()
        return None


if __name__ == "sitecustomize":
    held_at = os.environ[HOLD_AT]
    if held_at == AT_EXIT:
        # Exit handlers run last in, first out: this one, the first, runs last.
        atexit.register(_hold)
    else:
        sys.meta_path.insert(0, _HoldBeforeImport(held_at))
