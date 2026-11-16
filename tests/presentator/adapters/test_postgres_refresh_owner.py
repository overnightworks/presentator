"""The refresh owner on Postgres: one holder at a time, fenced, bounded, announced."""

import time
from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from presentator.adapters.identity import TokenIdentifierFactory
from presentator.adapters.postgres.deck_store import PostgresDeckStore
from presentator.adapters.postgres.locks import (
    REFRESH_LOCK,
    LockClass,
    lock_of,
    parameters,
)
from presentator.adapters.postgres.refresh_owner import PostgresRefreshOwnership
from presentator.adapters.postgres.source_runs import PostgresSourceRunStore
from presentator.adapters.postgres.sources import PostgresSourceStore
from presentator.adapters.secrets import secret_box
from presentator.contracts.decks import (
    SourceRun,
    SourceRunOutcome,
    ToolchainIdentity,
)
from presentator.ports.decks import (
    RefreshOwnershipLostError,
    RefreshOwnershipTimedOutError,
)
from tests.support.deck_source_support import a_build, a_deck
from tests.support.lobby import INSTANCE_KEY
from tests.support.postgres import (
    end_connections_holding,
    engine_on,
)

_BOUND = timedelta(milliseconds=300)
_WAIT_SECONDS = 30
_NOW = datetime(2026, 11, 16, 9, tzinfo=UTC)
_THE_TOOLCHAIN = ToolchainIdentity("a" * 64)
_ANOTHER_TOOLCHAIN = ToolchainIdentity("b" * 64)
_TAKE_AND_LET_GO_OF_THE_REFRESH_LOCK = text(
    "SELECT pg_try_advisory_lock(:lock_class, :instance)"
    " AND pg_advisory_unlock(:lock_class, :instance)"
)


@pytest.fixture
def engine(database_url: str) -> Generator[Engine]:
    each = engine_on(database_url)
    try:
        yield each
    finally:
        each.dispose()


@pytest.fixture
def owners(engine: Engine) -> Generator[Callable[..., PostgresRefreshOwnership]]:
    """Refresh owners on the one database, each closed at the end."""
    made: list[PostgresRefreshOwnership] = []

    def owner(
        toolchain: ToolchainIdentity = _THE_TOOLCHAIN,
    ) -> PostgresRefreshOwnership:
        each = PostgresRefreshOwnership(
            engine, toolchain=toolchain, lock_timeout=_BOUND
        )
        made.append(each)
        return each

    try:
        yield owner
    finally:
        for each in made:
            each.close()


def _until(accepted: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not accepted():
        assert time.monotonic() < deadline, "the database never reached the state"
        time.sleep(0.02)


def _holds(owner: PostgresRefreshOwnership) -> bool:
    with owner.try_hold() as held:
        return held


def _the_refresh_lock_is_free(engine: Engine) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.execute(
                _TAKE_AND_LET_GO_OF_THE_REFRESH_LOCK, parameters(REFRESH_LOCK)
            ).scalar_one()
        )


def _sweep(owner: PostgresRefreshOwnership) -> None:
    with owner.fenced():
        pass


def _a_run() -> SourceRun:
    return SourceRun(
        source_id="a-source",
        at=_NOW,
        outcome=SourceRunOutcome.SUCCESS,
        commit=None,
        reason=None,
    )


def test_one_owner_holds_the_refresh_and_another_is_turned_away(
    owners: Callable[..., PostgresRefreshOwnership],
) -> None:
    first, second = owners(), owners(_ANOTHER_TOOLCHAIN)

    with first.try_hold() as holding_first, second.try_hold() as holding_second:
        pass

    assert (holding_first, holding_second) == (True, False)
    assert _holds(second)


def test_a_removal_waits_for_the_holder_to_let_go_and_gives_up_at_its_bound(
    owners: Callable[..., PostgresRefreshOwnership],
) -> None:
    holder, remover = owners(), owners(_ANOTHER_TOOLCHAIN)

    with (
        holder.try_hold(),
        pytest.raises(RefreshOwnershipTimedOutError),
        remover.hold(),
    ):
        pass

    with remover.hold():
        pass


def test_a_holder_whose_connection_ended_is_refused_once_another_took_over(
    engine: Engine,
    tmp_path: Path,
    owners: Callable[..., PostgresRefreshOwnership],
) -> None:
    lost, taking_over = owners(), owners(_ANOTHER_TOOLCHAIN)
    decks = PostgresDeckStore(engine=engine, fence=lost)
    runs = PostgresSourceRunStore(engine=engine, fence=lost)
    sources = PostgresSourceStore(
        engine=engine,
        identifiers=TokenIdentifierFactory(),
        box=secret_box(INSTANCE_KEY),
        fence=lost,
    )
    refused = {
        "a deck row": lambda: decks.put(a_deck()),
        "a build": lambda: decks.put_build("a-slug", a_build(tmp_path)),
        "an attempt": lambda: decks.put_attempt("a-slug", None),
        "the marks of a source": lambda: decks.mark_removed_except(
            frozenset(), source_id="a-source", at=_NOW
        ),
        "the deletion of marked decks": lambda: decks.remove_marked_before(
            "a-source", before=_NOW
        ),
        "the decks of a source": lambda: decks.remove_for_source("a-source"),
        "a run": lambda: runs.record(_a_run()),
        "the runs of a source": lambda: runs.remove_for_source("a-source"),
        "a source": lambda: sources.remove("a-source"),
        "the sweep": lambda: _sweep(lost),
    }

    with lost.try_hold() as was_holding:
        end_connections_holding(engine, LockClass.REFRESH)
        _until(lambda: _holds(taking_over))
        outcomes: dict[str, bool] = {}
        for name, write in refused.items():
            try:
                write()
            except RefreshOwnershipLostError:
                outcomes[name] = True
            else:
                outcomes[name] = False

    assert was_holding
    assert outcomes == dict.fromkeys(refused, True)


def test_a_holder_writes_while_it_holds_and_nothing_once_it_let_go(
    engine: Engine, owners: Callable[..., PostgresRefreshOwnership]
) -> None:
    holder = owners()
    decks = PostgresDeckStore(engine=engine, fence=holder)

    with holder.try_hold():
        decks.put_attempt("no-such-deck", None)
    with pytest.raises(RefreshOwnershipLostError):
        decks.put_attempt("no-such-deck", None)


def test_a_bump_blocked_by_a_stale_transaction_gives_up_at_its_bound_and_succeeds_later(
    engine: Engine, owners: Callable[..., PostgresRefreshOwnership]
) -> None:
    owner = owners()

    with engine.connect() as stale:
        stale.execute(text("SELECT epoch FROM refresh_owner FOR SHARE"))
        held_while_blocked = _holds(owner)
        lock_free_after_giving_up = _the_refresh_lock_is_free(engine)

    assert not held_while_blocked
    assert lock_free_after_giving_up
    assert _holds(owner)


def test_a_toolchain_is_present_while_a_process_of_it_runs(
    owners: Callable[..., PostgresRefreshOwnership],
) -> None:
    asking = owners()
    another = owners(_ANOTHER_TOOLCHAIN)

    present = asking.toolchain_is_present(_ANOTHER_TOOLCHAIN)
    another.close()
    gone = asking.toolchain_is_present(_ANOTHER_TOOLCHAIN)

    assert (present, gone) == (True, False)


def test_a_dead_connection_is_replaced_and_announces_the_toolchain_again(
    engine: Engine, owners: Callable[..., PostgresRefreshOwnership]
) -> None:
    owner = owners()
    asking = owners(_ANOTHER_TOOLCHAIN)

    with owner.try_hold():
        end_connections_holding(engine, LockClass.REFRESH)
        _until(lambda: not asking.toolchain_is_present(_THE_TOOLCHAIN))
    owner.recover()

    assert asking.toolchain_is_present(_THE_TOOLCHAIN)
    assert _holds(owner)


def test_a_holder_whose_connection_ended_leaves_its_block_without_failing(
    engine: Engine, owners: Callable[..., PostgresRefreshOwnership]
) -> None:
    owner = owners()

    with owner.try_hold() as held:
        end_connections_holding(engine, LockClass.REFRESH)
        _until(lambda: _the_refresh_lock_is_free(engine))

    assert held


def test_a_pinned_connection_that_ended_outside_a_hold_is_replaced_by_recovery(
    engine: Engine, owners: Callable[..., PostgresRefreshOwnership]
) -> None:
    owner = owners()

    end_connections_holding(engine, LockClass.TOOLCHAIN_PRESENCE)
    asking = owners(_ANOTHER_TOOLCHAIN)
    _until(lambda: not asking.toolchain_is_present(_THE_TOOLCHAIN))
    owner.recover()

    assert asking.toolchain_is_present(_THE_TOOLCHAIN)
    assert _holds(owner)


def test_a_sweep_that_still_holds_the_refresh_keeps_another_from_taking_it_over(
    engine: Engine, owners: Callable[..., PostgresRefreshOwnership]
) -> None:
    sweeping, taking_over = owners(), owners(_ANOTHER_TOOLCHAIN)

    with sweeping.try_hold():
        end_connections_holding(engine, LockClass.REFRESH)
        _until(lambda: _the_refresh_lock_is_free(engine))
        with sweeping.fenced():
            taken_over_meanwhile = _holds(taking_over)
    taken_over_after = _holds(taking_over)

    assert (taken_over_meanwhile, taken_over_after) == (False, True)


def test_a_take_that_ended_with_its_connection_fails_and_the_next_one_reconnects(
    engine: Engine, owners: Callable[..., PostgresRefreshOwnership]
) -> None:
    owner = owners()

    end_connections_holding(engine, LockClass.TOOLCHAIN_PRESENCE)
    with pytest.raises(DBAPIError):
        _holds(owner)

    assert _holds(owner)


def test_an_epoch_that_cannot_grow_fails_the_take_and_lets_the_lock_go(
    engine: Engine, owners: Callable[..., PostgresRefreshOwnership]
) -> None:
    owner = owners()
    largest_epoch = 2**63 - 1
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE refresh_owner SET epoch = :epoch"), {"epoch": largest_epoch}
        )

    with pytest.raises(DBAPIError):
        _holds(owner)

    assert _the_refresh_lock_is_free(engine)


def test_a_holder_that_lost_its_connection_inside_its_block_announces_nothing_more(
    engine: Engine, owners: Callable[..., PostgresRefreshOwnership]
) -> None:
    owner = owners()

    with owner.try_hold() as held:
        end_connections_holding(engine, LockClass.REFRESH, LockClass.TOOLCHAIN_PRESENCE)
        asking = owners(_ANOTHER_TOOLCHAIN)
        with pytest.raises(DBAPIError):
            owner.toolchain_is_present(_ANOTHER_TOOLCHAIN)
    _until(lambda: not asking.toolchain_is_present(_THE_TOOLCHAIN))

    assert held
    assert _the_refresh_lock_is_free(engine)


def test_an_owner_whose_toolchain_key_is_held_exclusively_is_not_made(
    engine: Engine,
) -> None:
    key = parameters(lock_of(LockClass.TOOLCHAIN_PRESENCE, _THE_TOOLCHAIN.digest))
    with engine.connect() as another_process:
        another_process.execute(
            text("SELECT pg_advisory_lock(:lock_class, :instance)"), key
        )

        with pytest.raises(DBAPIError):
            PostgresRefreshOwnership(
                engine, toolchain=_THE_TOOLCHAIN, lock_timeout=_BOUND
            )
