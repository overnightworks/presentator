"""Refresh ownership where one process serves the database, as the SQLite file does."""

from contextlib import AbstractContextManager, nullcontext

from presentator.contracts.decks import ToolchainIdentity


class SingleProcessRefreshOwnership:
    """The one process holds the refresh whenever its own lock lets it."""

    def try_hold(self) -> AbstractContextManager[bool]:
        """Hold nothing more than the process's own lock, which is held already."""
        return nullcontext(enter_result=True)

    def hold(self) -> AbstractContextManager[None]:
        """Hold nothing more than the process's own lock, which is held already."""
        return nullcontext()

    def recover(self) -> None:
        """There is no link to check."""

    def fenced(self) -> AbstractContextManager[None]:
        """No other process can take the refresh over."""
        return nullcontext()

    def toolchain_is_present(self, _toolchain: ToolchainIdentity, /) -> bool:
        """No other process builds with any toolchain."""
        return False
