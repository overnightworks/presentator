"""The k3d driver's lives: the watch of the worker pod and each life's judgement."""

import json
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path
from types import ModuleType

import pytest

from presentator.adapters.build_worker import CONNECT_WINDOW_SECONDS
from presentator.host.worker import REFUSAL_EXIT_STATUS
from presentator.host.worker_self_check import Predicate
from tests.scripts.support.k3d_world import (
    Document,
    Fixture,
    Version,
    World,
    fixture_maker,
    life_events,
    lives_stream,
    pod_version,
    terminated_state,
    watch_event,
    workload_documents,
)


@pytest.fixture
def lives_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d_lives")


@pytest.fixture
def make_fixture(
    load_script: Callable[..., ModuleType], tmp_path: Path
) -> Callable[..., Fixture]:
    return fixture_maker(load_script, tmp_path)


def judge_cell(  # noqa: PLR0913 - each argument is one knob of the judged cell
    make_fixture: Callable[..., Fixture],
    lives_module: ModuleType,
    events: list[Document],
    refusal: Predicate | None,
    *,
    init_container: bool = True,
    lives: int = 1,
) -> tuple[str, World]:
    """Judge one cell over the watch's events; give its problem and the world."""
    world = World(streams=[events])
    fixture = make_fixture(world)
    expectation = lives_module.Expectation(
        refusal=refusal, init_container=init_container, lives=lives
    )
    documents = workload_documents(world.production_image)
    return fixture.driver.drive_cell(fixture.run, 8, documents, expectation), world


REFUSED = Predicate.NO_ROUTE


refused_life = partial(life_events, "one", REFUSED)
passing_life = partial(life_events, "one", None)
GONE = terminated_state(137, reason="ContainerStatusUnknown")
SECOND = Version(last_state=terminated_state(started=9))
UNTERMINATED = [
    watch_event("ADDED", pod_version("one")),
    watch_event("DELETED", pod_version("one")),
]


@pytest.mark.parametrize(
    ("refusal", "events", "fragment"),
    [
        (REFUSED, refused_life(ended=terminated_state(1)), "exit status 1"),
        (REFUSED, passing_life(), "exit status 0"),
        (REFUSED, life_events("one", Predicate.ALONE), Predicate.ALONE),
        (REFUSED, refused_life(ended=terminated_state()), "start refused"),
        (REFUSED, refused_life(version=Version(init_exit=1)), "remove-link ended as 1"),
        (REFUSED, refused_life(version=Version(init=False)), "lacks an init container"),
        (REFUSED, refused_life(version=Version(restarts=1)), "restartCount is 1"),
        (REFUSED, refused_life(version=SECOND), "terminated 2 times"),
        (REFUSED, refused_life(ended=GONE), "ContainerStatusUnknown"),
        (REFUSED, UNTERMINATED, "deleted before its worker terminated"),
        (None, refused_life(), f"exit status {REFUSAL_EXIT_STATUS}"),
        (None, passing_life(ended=terminated_state(1)), "exit status 1"),
    ],
    ids=[
        "another exit status",
        "a passing life where a refusal is expected",
        "another predicate",
        "an empty message",
        "remove-link not ended 0",
        "the Pod has no init container",
        "a restart inside one uid",
        "a second termination inside one uid",
        "a lost sandbox",
        "deleted before the worker terminated",
        "a refusal where a pass is expected",
        "another exit status where a pass is expected",
    ],
)
def test_a_life_that_is_not_the_expected_one_fails_naming_its_uid_and_the_reason(
    make_fixture: Callable[..., Fixture],
    lives_module: ModuleType,
    refusal: Predicate | None,
    events: list[Document],
    fragment: str,
) -> None:
    problem, world = judge_cell(make_fixture, lives_module, events, refusal)
    assert "uid one" in problem
    assert fragment in problem
    assert world.issued("kubectl", "get", "events") != []
    assert world.applied("Pod") != []


@pytest.mark.parametrize(
    ("gap", "passes"),
    [
        (CONNECT_WINDOW_SECONDS - 1, True),
        (CONNECT_WINDOW_SECONDS, False),
        (CONNECT_WINDOW_SECONDS + 30, False),
    ],
    ids=["just below the window", "at the window", "above the window"],
)
def test_a_successor_must_start_below_the_connect_window(
    make_fixture: Callable[..., Fixture],
    lives_module: ModuleType,
    *,
    gap: float,
    passes: bool,
) -> None:
    events = lives_stream(2, REFUSED, gap=int(gap))
    problem, _ = judge_cell(make_fixture, lives_module, events, REFUSED, lives=2)
    assert (problem == "") is passes
    assert passes or f"{gap:g} s" in problem


@pytest.mark.parametrize(
    "after",
    [[], [{"type": "ERROR", "object": {"kind": "Status"}}]],
    ids=["end", "ERROR"],
)
def test_a_watch_that_ends_early_fails_naming_the_lives_and_asks_for_the_diagnostics(
    make_fixture: Callable[..., Fixture],
    lives_module: ModuleType,
    after: list[Document],
) -> None:
    events = lives_stream(2, REFUSED) + after
    problem, world = judge_cell(make_fixture, lives_module, events, REFUSED, lives=3)
    assert "2 of 3 lives" in problem
    assert "life-0" in problem
    assert "life-1" in problem
    assert world.issued("kubectl", "get", "events") != []
    assert world.applied("Pod") != []


def test_the_driver_stops_after_its_lives_and_closes_the_watch(
    make_fixture: Callable[..., Fixture],
    lives_module: ModuleType,
) -> None:
    judged = lives_stream(2, REFUSED)
    later = life_events("later", REFUSED, ended=terminated_state(1))
    problem, world = judge_cell(
        make_fixture, lives_module, judged + later, REFUSED, lives=2
    )
    assert problem == ""
    assert world.events_printed == len(judged)
    assert world.watches_closed == 1


def test_a_wrong_predicate_fails_the_cell_and_requests_the_events_and_the_twin_pod(
    lives_module: ModuleType,
    make_fixture: Callable[..., Fixture],
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = World(
        streams=[
            life_events("one", Predicate.MOUNTS, version=Version(init=False)),
            lives_stream(lives_module.LIVES, REFUSED),
        ]
    )
    fixture = make_fixture(world)
    with pytest.raises(SystemExit):
        fixture.drive()
    output = capsys.readouterr()
    assert Predicate.MOUNTS in output.err
    assert "cell 7" in output.out
    assert world.issued("kubectl", "get", "events") != []
    twin = world.applied("Pod")[0]
    assert "/proc/self/mountinfo" in twin["spec"]["containers"][0]["command"][-1]
    assert "initContainers" not in twin["spec"]


def test_the_watch_hands_out_an_event_before_the_stream_goes_on(
    lives_module: ModuleType,
) -> None:
    first = watch_event("ADDED", pod_version("one"))
    second = watch_event("DELETED", pod_version("one"))

    def lines() -> Iterator[str]:
        yield from f"{json.dumps(first, indent=4)}\n".splitlines(keepends=True)
        yield json.dumps(second)
        message = "the stream was read on before the first event was handed out"
        raise AssertionError(message)

    events = lives_module.watch_events(lines())
    assert next(events) == first
    assert next(events) == second


def test_a_cut_watch_keeps_the_events_before_the_cut(
    lives_module: ModuleType,
) -> None:
    lines = ['{"type": "ADDED"}', '{"type": "ADD']
    assert list(lives_module.watch_events(lines)) == [{"type": "ADDED"}]
