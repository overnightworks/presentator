"""The refresh owner of an instance whose other processes a test arranges."""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field

from presentator.contracts.decks import ToolchainIdentity
from presentator.ports.decks import (
    RefreshOwnershipLostError,
    RefreshOwnershipTimedOutError,
)


@dataclass(kw_only=True)
class FakeRefreshOwnership:
    """The refresh owner of an instance whose other processes a test arranges."""

    held_elsewhere: bool = False
    lost_to_another: bool = False
    present: frozenset[ToolchainIdentity] = frozenset()
    holding: bool = field(default=False, init=False)

    @contextmanager
    def try_hold(self) -> Generator[bool]:
        if self.held_elsewhere:
            yield False
            return
        self.holding = True
        try:
            yield True
        finally:
            self.holding = False

    @contextmanager
    def hold(self) -> Generator[None]:
        if self.held_elsewhere:
            raise RefreshOwnershipTimedOutError
        self.holding = True
        try:
            yield
        finally:
            self.holding = False

    def recover(self) -> None:
        return None

    @contextmanager
    def fenced(self) -> Generator[None]:
        if self.lost_to_another:
            raise RefreshOwnershipLostError
        yield

    def toolchain_is_present(self, toolchain: ToolchainIdentity) -> bool:
        return toolchain in self.present
