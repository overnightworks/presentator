"""The refresh ownership of an instance served by one process holds nothing back."""

from presentator.adapters.refresh_ownership import SingleProcessRefreshOwnership
from presentator.contracts.decks import ToolchainIdentity


def test_the_one_process_holds_the_refresh_and_finds_no_toolchain_elsewhere() -> None:
    ownership = SingleProcessRefreshOwnership()

    with ownership.try_hold() as holding_if_free, ownership.hold(), ownership.fenced():
        pass
    ownership.recover()

    assert holding_if_free
    assert not ownership.toolchain_is_present(ToolchainIdentity("a" * 64))
