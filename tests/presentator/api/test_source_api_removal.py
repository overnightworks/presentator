"""An admin removes a source through the JSON API, with the counts they saw."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from http import HTTPStatus
from pathlib import Path
from threading import RLock, Thread
from typing import cast

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from presentator.api.problems import PROBLEM_MEDIA_TYPE
from presentator.api.source_api import (
    SourceRemovalRefusedProblem,
    SourceRemovalStaleProblem,
)
from presentator.application.decks import (
    RESERVED_SOURCE_NAME,
    Decks,
    SourceRemovalRefusal,
)
from presentator.contracts.decks import Build, Deck, DeckFolder, Source
from tests.presentator.api.support.source_api_support import (
    ASKING_FOR_JSON,
    LIST,
    PAGE,
    REMOVAL,
    SAME_ORIGIN,
    Instance,
    a_bearer,
    a_client_with_no_header_of_its_own,
    a_created_source,
    a_member_over,
    a_scene_that_must_not_run,
    a_world,
    an_admin_bearer_instance,
    an_admin_cookie_instance,
    an_admin_over,
    assert_nothing_returned,
    assert_problem,
    assert_validation_answer,
    calling_hook,
    decks_that_must_not_run,
    preview_removal,
    remove,
)
from tests.presentator.application.support.decks_support import a_folder, a_source
from tests.presentator.application.support.fakes import (
    FakeBuildRunner,
    FakeDeckFolders,
    FakeDeckStore,
    FakeSourceStore,
    FrozenClock,
    HeldDeckFolders,
)
from tests.support.lobby import ADMIN, NOW, GivenDecks, a_decks
from tests.support.sign_in_support import ADMIN as ADMIN_GROUPS
from tests.support.sign_in_support import MEMBER, ContentionSignalingLock

_BOUND = 30.0

_NOT_SENT_BY_THE_SAME_ORIGIN = pytest.mark.parametrize(
    "headers",
    [
        pytest.param(
            {"sec-fetch-site": "cross-site", "origin": "https://testserver"},
            id="cross-site",
        ),
        pytest.param(
            {"sec-fetch-site": "same-site", "origin": "https://elsewhere.invalid"},
            id="same-site-with-a-foreign-origin",
        ),
        pytest.param({}, id="no-claim-at-all"),
    ],
)


@dataclass(frozen=True, slots=True)
class Stocked:
    """Two sources with decks, built talks and runs, over fakes the test reads."""

    decks: Decks
    sources: FakeSourceStore
    folders: FakeDeckFolders
    store: FakeDeckStore
    builder: FakeBuildRunner
    going: Source
    staying: Source
    credential: str
    rounds: tuple[tuple[DeckFolder, ...], ...]

    @property
    def name(self) -> str:
        return self.going.name

    @property
    def first_round(self) -> tuple[DeckFolder, ...]:
        """The folders of the first run, which every later run still carries."""
        return self.rounds[0]

    @property
    def deck_count(self) -> int:
        return len(self.first_round)

    @property
    def run_count(self) -> int:
        return len(self.rounds)

    def runs_of(self, source: Source) -> frozenset[str]:
        """The run directories the source's decks deliver from."""
        runs = (
            self.builder.run_of(deck.build.directory)
            for deck in self.store.for_source(source.id)
            if deck.build is not None
        )
        return frozenset(run.name for run in runs if run is not None)

    def refresh(self) -> None:
        assert self.decks.refresh_named(self.name)

    def preview(self) -> tuple[int, int]:
        preview = self.decks.removal(self.name)
        assert preview is not None
        return preview.deck_count, preview.run_count


def stocked() -> Stocked:
    """A source of three decks (one marked removed, two owners) and two runs."""
    going = a_source("talks", identifier="going")
    staying = a_source("others", identifier="staying")
    credential = "the-access-secret-this-scenario-stores"
    first_round = tuple(a_folder(name) for name in ("first", "second", "third"))
    rounds = (first_round, first_round[:2])
    sources = FakeSourceStore(
        sources=[going, staying],
        hashes={going.name: b"a-hash", staying.name: b"another-hash"},
        secrets={going.id: credential, staying.id: "the-other-access-secret"},
    )
    folders = FakeDeckFolders(carried={staying.id: (a_folder("kept"),)})
    store = FakeDeckStore()
    builder = FakeBuildRunner()
    decks = replace(
        a_decks(
            GivenDecks(sources=(going, staying), store=store, builder=builder),
            FrozenClock(instant=NOW),
        ),
        sources=sources,
        folders=folders,
    )
    for carried in rounds:
        folders.carried[going.id] = carried
        decks.refresh()
    store.kept["second"] = replace(store.kept["second"], owner_id="another-owner")
    return Stocked(
        decks, sources, folders, store, builder, going, staying, credential, rounds
    )


def assert_rows_stand(shelf: Stocked) -> None:
    """Both sources, their decks and runs are all still there."""
    assert {each.name for each in shelf.decks.listed_sources()} == {
        shelf.going.name,
        shelf.staying.name,
    }
    assert shelf.store.for_source(shelf.going.id)
    assert shelf.decks.source_runs.recent(shelf.going.id)
    assert shelf.store.for_source(shelf.staying.id)
    assert shelf.decks.source_runs.recent(shelf.staying.id)


def assert_nothing_removed(shelf: Stocked) -> None:
    assert_rows_stand(shelf)
    assert shelf.preview() == (shelf.deck_count, shelf.run_count)
    assert shelf.folders.forgotten == []
    assert shelf.builder.removed == []


def assert_removed_exactly(shelf: Stocked, runs_before: frozenset[str]) -> None:
    """The going source is gone from rows and disk; the staying one is whole."""
    assert [each.name for each in shelf.decks.listed_sources()] == [shelf.staying.name]
    assert shelf.store.for_source(shelf.going.id) == ()
    assert shelf.decks.source_runs.recent(shelf.going.id) == ()
    assert shelf.decks.removal(shelf.going.name) is None
    assert set(shelf.folders.forgotten) == {shelf.going.id}
    assert {run.name for run in shelf.builder.removed} == runs_before
    assert shelf.store.for_source(shelf.staying.id)
    assert shelf.decks.source_runs.recent(shelf.staying.id)
    assert {run.name for run in shelf.builder.on_disk} == shelf.runs_of(shelf.staying)


def _by_cookie(shelf: Stocked, _tmp_path: Path) -> Instance:
    return an_admin_cookie_instance(shelf.decks, shelf.sources)


def _by_bearer(shelf: Stocked, tmp_path: Path) -> Instance:
    return an_admin_bearer_instance(shelf.decks, shelf.sources, tmp_path)


AN_ADMIN_BY_COOKIE_OR_BEARER = pytest.mark.parametrize(
    "reach", [_by_cookie, _by_bearer], ids=["cookie", "bearer"]
)

type Gated = Callable[[TestClient, dict[str, str]], Response]


def _previewing(client: TestClient, headers: dict[str, str]) -> Response:
    return preview_removal(client, "talks", headers)


def _removing(client: TestClient, headers: dict[str, str]) -> Response:
    return remove(client, "talks", deck_count=1, run_count=1, headers=headers)


_BOTH_ROUTES = pytest.mark.parametrize(
    "act", [_previewing, _removing], ids=["preview", "delete"]
)


def _removing_with_the_previews_counts(shelf: Stocked, client: TestClient) -> Response:
    deck_count, run_count = shelf.preview()
    return remove(client, shelf.name, deck_count=deck_count, run_count=run_count)


# 1
@_BOTH_ROUTES
def test_a_signed_out_caller_meets_webauths_answer_not_a_problem(act: Gated) -> None:
    client = an_admin_over(decks_that_must_not_run()).client
    client.cookies.clear()

    asking_json = act(client, ASKING_FOR_JSON)
    asking_html = act(client, {})

    assert asking_json.status_code == HTTPStatus.UNAUTHORIZED
    assert asking_html.status_code == HTTPStatus.FOUND
    for answer in (asking_json, asking_html):
        assert answer.headers.get("content-type") != PROBLEM_MEDIA_TYPE


# 2
@_BOTH_ROUTES
def test_a_member_is_refused_with_a_problem(act: Gated) -> None:
    client = a_member_over(decks_that_must_not_run()).client

    assert_problem(act(client, {}), HTTPStatus.FORBIDDEN)


# 3
@_BOTH_ROUTES
def test_a_members_bearer_is_refused_with_a_problem(act: Gated, tmp_path: Path) -> None:
    scene = a_scene_that_must_not_run(tmp_path)

    answer = act(a_client_with_no_header_of_its_own(scene), a_bearer(scene, MEMBER))

    assert_problem(answer, HTTPStatus.FORBIDDEN)


# 4
def test_a_members_bearer_beside_an_admin_cookie_carries_the_members_rights(
    tmp_path: Path,
) -> None:
    scene = a_scene_that_must_not_run(tmp_path)
    browser = scene.browser()
    scene.sign_in(browser, groups=ADMIN_GROUPS)

    answer = _removing(browser, {**a_bearer(scene, MEMBER), **SAME_ORIGIN})

    assert_problem(answer, HTTPStatus.FORBIDDEN)


# 5
def test_an_invalid_bearer_beside_an_admin_cookie_is_refused(tmp_path: Path) -> None:
    scene = a_scene_that_must_not_run(tmp_path)
    browser = scene.browser()
    scene.sign_in(browser, groups=ADMIN_GROUPS)

    answer = _removing(browser, {"authorization": "Bearer nonsense", **SAME_ORIGIN})

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert 'error="invalid_token"' in answer.headers["www-authenticate"]


# 6
@_NOT_SENT_BY_THE_SAME_ORIGIN
def test_the_origin_check_refuses_a_removal_before_any_port_is_touched(
    headers: dict[str, str],
) -> None:
    lobby = an_admin_over(decks_that_must_not_run(), headers={"accept": "text/html"})

    answer = _removing(lobby.client, headers)

    assert answer.status_code == HTTPStatus.FORBIDDEN


# 7
def test_a_same_site_removal_with_the_own_origin_is_taken() -> None:
    shelf = stocked()
    lobby = an_admin_over(shelf.decks, headers={"accept": "text/html"})
    deck_count, run_count = shelf.preview()

    answer = remove(
        lobby.client,
        shelf.name,
        deck_count=deck_count,
        run_count=run_count,
        headers={"sec-fetch-site": "same-site", "origin": "https://testserver"},
    )

    assert answer.status_code == HTTPStatus.NO_CONTENT


# 8
def test_the_preview_counts_every_deck_of_every_owner_and_changes_nothing() -> None:
    shelf = stocked()
    client = an_admin_over(shelf.decks).client

    answer = preview_removal(client, shelf.name)

    assert answer.status_code == HTTPStatus.OK
    counts = answer.json()
    assert counts.keys() == {"deck_count", "run_count"}
    assert counts["deck_count"] == shelf.deck_count
    assert counts["run_count"] == shelf.run_count
    assert_nothing_removed(shelf)


# 9, 10
@AN_ADMIN_BY_COOKIE_OR_BEARER
def test_a_removal_with_the_previews_counts_removes_exactly_the_source(
    reach: Callable[[Stocked, Path], Instance], tmp_path: Path
) -> None:
    shelf = stocked()
    instance = reach(shelf, tmp_path)
    runs_before = shelf.runs_of(shelf.going)
    previewed = preview_removal(instance.client, shelf.name, instance.headers).json()

    answer = remove(instance.client, shelf.name, headers=instance.headers, **previewed)

    assert answer.status_code == HTTPStatus.NO_CONTENT
    assert answer.content == b""
    assert_removed_exactly(shelf, runs_before)
    listed = instance.client.get(LIST, headers={**instance.headers, **ASKING_FOR_JSON})
    assert [each["name"] for each in listed.json()["sources"]] == [shelf.staying.name]
    gone = instance.client.get(
        PAGE.format(name=shelf.name), headers={**instance.headers, **ASKING_FOR_JSON}
    )
    assert_problem(gone, HTTPStatus.NOT_FOUND)


def test_a_removed_sources_talk_answers_not_found(tmp_path: Path) -> None:
    source = a_source("talks", identifier="going")
    built = tmp_path / "builds" / "run" / "out"
    (built / "talk").mkdir(parents=True)
    (built / "talk" / "index.html").write_text("<html></html>", encoding="utf-8")
    store = FakeDeckStore()
    store.put(
        Deck(
            slug="kundenfeedback",
            title="A talk",
            changed_at=NOW,
            owner_id=ADMIN,
            source_id=source.id,
            commit="a-commit",
            build=None,
            attempt=None,
        )
    )
    store.put_build(
        "kundenfeedback",
        Build(
            directory=built / "talk",
            pdf=built / "deck.pdf",
            commit="a-commit",
            built_at=NOW,
        ),
    )
    decks = a_decks(
        GivenDecks(
            sources=(source,),
            store=store,
            builder=FakeBuildRunner(root=tmp_path / "builds"),
        ),
        FrozenClock(instant=NOW),
    )
    client = an_admin_over(decks).client
    preview = preview_removal(client, source.name).json()

    before = client.get("/deck/kundenfeedback")
    removed = remove(client, source.name, **preview)
    after = client.get("/deck/kundenfeedback")

    assert removed.status_code == HTTPStatus.NO_CONTENT
    assert before.status_code == HTTPStatus.OK
    assert after.status_code == HTTPStatus.NOT_FOUND


def test_a_removed_sources_hook_is_gone_and_its_name_and_url_can_be_taken_again() -> (
    None
):
    world = a_world()
    client = an_admin_over(world.decks).client
    first_secret = a_created_source(client)
    name = world.store.sources[0].name
    assert (
        calling_hook(client, name, offered=first_secret).status_code
        == HTTPStatus.NO_CONTENT
    )
    preview = preview_removal(client, name).json()

    removed = remove(client, name, **preview)
    hook = calling_hook(client, name, offered=first_secret)
    second_secret = a_created_source(client)

    assert removed.status_code == HTTPStatus.NO_CONTENT
    assert hook.status_code == HTTPStatus.NOT_FOUND
    assert second_secret != first_secret


# 12, 13
@pytest.mark.parametrize("count", ["deck_count", "run_count"])
@pytest.mark.parametrize("change", [1, -1], ids=["one-above", "one-below"])
def test_counts_that_differ_answer_the_current_ones_and_remove_nothing(
    count: str, change: int
) -> None:
    shelf = stocked()
    client = an_admin_over(shelf.decks).client
    deck_count, run_count = shelf.preview()
    if count == "deck_count":
        deck_count += change
    else:
        run_count += change

    answer = remove(client, shelf.name, deck_count=deck_count, run_count=run_count)

    assert_problem(answer, HTTPStatus.CONFLICT)
    assert answer.json() == {
        "type": SourceRemovalStaleProblem(deck_count=0, run_count=0).type,
        "title": HTTPStatus.CONFLICT.phrase,
        "status": HTTPStatus.CONFLICT.value,
        "deck_count": shelf.deck_count,
        "run_count": shelf.run_count,
    }
    assert_nothing_removed(shelf)


# 14
def test_a_deck_added_after_the_preview_makes_it_stale_until_confirmed_again() -> None:
    shelf = stocked()
    client = an_admin_over(shelf.decks).client
    seen = shelf.preview()
    shelf.folders.carried[shelf.going.id] = (*shelf.first_round, a_folder("fresh"))
    shelf.refresh()
    runs_before = shelf.runs_of(shelf.going)
    now = shelf.preview()

    stale = remove(client, shelf.name, deck_count=seen[0], run_count=seen[1])
    current = stale.json()
    again = remove(
        client,
        shelf.name,
        deck_count=current["deck_count"],
        run_count=current["run_count"],
    )

    assert_problem(stale, HTTPStatus.CONFLICT)
    assert current["deck_count"] == now[0]
    assert current["run_count"] == now[1]
    assert current["deck_count"] == seen[0] + 1
    assert again.status_code == HTTPStatus.NO_CONTENT
    assert_removed_exactly(shelf, runs_before)


# 15
def test_a_run_after_the_preview_changes_nothing_once_three_are_kept() -> None:
    shelf = stocked()
    client = an_admin_over(shelf.decks).client
    shelf.refresh()
    seen = shelf.preview()
    shelf.refresh()
    runs_before = shelf.runs_of(shelf.going)

    answer = remove(client, shelf.name, deck_count=seen[0], run_count=seen[1])

    assert answer.status_code == HTTPStatus.NO_CONTENT
    assert_removed_exactly(shelf, runs_before)


# 16
@pytest.mark.parametrize(
    ("params", "body"),
    [
        pytest.param({}, None, id="no-counts"),
        pytest.param({"deck_count": 1}, None, id="run-count-missing"),
        pytest.param({"run_count": 1}, None, id="deck-count-missing"),
        pytest.param({"deck_count": "abc", "run_count": 1}, None, id="not-a-number"),
        pytest.param({"deck_count": 1.5, "run_count": 1}, None, id="a-fraction"),
        pytest.param({"deck_count": 1, "run_count": -1}, None, id="negative"),
        pytest.param({}, {"deck_count": 1, "run_count": 1}, id="counts-in-a-body"),
    ],
)
def test_missing_or_malformed_counts_are_a_validation_answer_before_any_port(
    params: dict[str, str | int | float], body: object
) -> None:
    client = an_admin_over(decks_that_must_not_run()).client

    answer = client.request(
        "DELETE", PAGE.format(name="talks"), params=params, json=body
    )

    assert_validation_answer(answer)


# 17
@pytest.mark.parametrize("name", ["no-such-source", RESERVED_SOURCE_NAME])
def test_an_unknown_or_reserved_name_is_a_problem_to_preview_and_to_remove(
    name: str,
) -> None:
    shelf = stocked()
    client = an_admin_over(shelf.decks).client

    assert_problem(preview_removal(client, name), HTTPStatus.NOT_FOUND)
    assert_problem(
        remove(client, name, deck_count=0, run_count=0), HTTPStatus.NOT_FOUND
    )
    assert_nothing_removed(shelf)


# 18
def test_a_name_of_more_than_one_segment_is_the_frameworks_not_found() -> None:
    client = an_admin_over(decks_that_must_not_run()).client

    previewed = client.get(REMOVAL.format(name="a/b"))
    removed = client.delete(
        PAGE.format(name="a/b"), params={"deck_count": 0, "run_count": 0}
    )

    for answer in (previewed, removed):
        assert answer.status_code == HTTPStatus.NOT_FOUND
        assert answer.headers.get("content-type") != PROBLEM_MEDIA_TYPE


# 19
def test_a_source_removed_after_the_preview_is_a_problem() -> None:
    shelf = stocked()
    client = an_admin_over(shelf.decks).client
    seen = shelf.preview()
    first = remove(client, shelf.name, deck_count=seen[0], run_count=seen[1])

    second = remove(client, shelf.name, deck_count=seen[0], run_count=seen[1])

    assert first.status_code == HTTPStatus.NO_CONTENT
    assert_problem(second, HTTPStatus.NOT_FOUND)


# 20
def test_two_removals_waiting_on_the_lock_remove_once_and_the_other_finds_nothing() -> (
    None
):
    shelf = stocked()
    lock = ContentionSignalingLock()
    decks = replace(shelf.decks, one_at_a_time=cast("RLock", lock))
    seen = shelf.preview()
    answers: list[Response] = []
    clients = [an_admin_over(decks).client for _ in range(2)]

    def removing(client: TestClient) -> None:
        answers.append(
            remove(client, shelf.name, deck_count=seen[0], run_count=seen[1])
        )

    callers = [Thread(target=removing, args=(client,)) for client in clients]
    with lock:
        for caller in callers:
            caller.start()
        for _ in callers:
            assert lock.held_up.acquire(timeout=_BOUND)
    for caller in callers:
        caller.join()

    assert sorted(answer.status_code for answer in answers) == [
        HTTPStatus.NO_CONTENT,
        HTTPStatus.NOT_FOUND,
    ]
    assert not [
        answer for answer in answers if answer.status_code == HTTPStatus.CONFLICT
    ]


# 21
def test_a_removal_waits_for_a_refresh_then_answers_the_fresh_counts() -> None:
    shelf = stocked()
    held = HeldDeckFolders(found=(*shelf.first_round, a_folder("fresh")))
    lock = ContentionSignalingLock()
    decks = replace(shelf.decks, folders=held, one_at_a_time=cast("RLock", lock))
    seen = shelf.preview()
    answers: list[Response] = []
    client = an_admin_over(decks).client
    refreshing = Thread(target=lambda: decks.refresh_named(shelf.name))
    refreshing.start()
    assert held.entered.wait(_BOUND)

    def removing() -> None:
        answers.append(
            remove(client, shelf.name, deck_count=seen[0], run_count=seen[1])
        )

    removal = Thread(target=removing)
    removal.start()
    assert lock.held_up.acquire(timeout=_BOUND)
    held.release.set()
    refreshing.join()
    removal.join()

    (answer,) = answers
    assert_problem(answer, HTTPStatus.CONFLICT)
    assert answer.json()["deck_count"] == seen[0] + 1
    assert_rows_stand(replace(shelf, decks=decks))


def _the_mirror_will_not_go(shelf: Stocked) -> None:
    shelf.folders.stuck = frozenset({shelf.going.id})


def _a_run_will_not_go(shelf: Stocked) -> None:
    shelf.builder.stuck = frozenset(
        run for run in shelf.builder.on_disk if run.name in shelf.runs_of(shelf.going)
    )


def _a_build_fits_no_run(shelf: Stocked) -> None:
    deck = shelf.store.kept["first"]
    assert deck.build is not None
    shelf.store.kept["first"] = replace(
        deck, build=replace(deck.build, directory=Path("/nowhere/a-run/out/talk"))
    )


# 22, 23, 24, 26
@pytest.mark.parametrize(
    "stick",
    [_the_mirror_will_not_go, _a_run_will_not_go],
    ids=["mirror", "run-directory"],
)
def test_a_disk_leftover_is_a_problem_that_keeps_every_row_and_a_repeat_finishes(
    stick: Callable[[Stocked], None],
) -> None:
    shelf = stocked()
    client = an_admin_over(shelf.decks).client
    runs_before = shelf.runs_of(shelf.going)
    confirmed = shelf.preview()
    stick(shelf)

    refused = remove(
        client, shelf.name, deck_count=confirmed[0], run_count=confirmed[1]
    )

    assert_problem(refused, HTTPStatus.INTERNAL_SERVER_ERROR)
    assert refused.json()["reason"] == SourceRemovalRefusal.DISK_LEFTOVER
    assert_nothing_returned(refused, str(shelf.builder.root))
    assert_rows_stand(shelf)
    assert shelf.preview() == confirmed
    assert client.get(PAGE.format(name=shelf.name)).status_code == HTTPStatus.OK
    assert shelf.builder.removed

    shelf.folders.stuck = frozenset()
    shelf.builder.stuck = frozenset()
    repeated = remove(
        client, shelf.name, deck_count=confirmed[0], run_count=confirmed[1]
    )

    assert repeated.status_code == HTTPStatus.NO_CONTENT
    assert_removed_exactly(shelf, runs_before)


def test_a_build_that_fits_no_run_refuses_the_removal_and_deletes_no_row() -> None:
    shelf = stocked()
    client = an_admin_over(shelf.decks).client
    _a_build_fits_no_run(shelf)

    refused = _removing_with_the_previews_counts(shelf, client)

    assert_problem(refused, HTTPStatus.INTERNAL_SERVER_ERROR)
    assert_rows_stand(shelf)


# 25
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", PAGE),
        ("PATCH", PAGE),
        ("POST", PAGE),
        ("POST", REMOVAL),
        ("DELETE", REMOVAL),
    ],
)
def test_the_other_methods_are_the_frameworks_405_and_remove_nothing(
    method: str, path: str
) -> None:
    shelf = stocked()
    client = an_admin_over(shelf.decks).client

    answer = client.request(
        method, path.format(name=shelf.name), params={"deck_count": 0, "run_count": 0}
    )

    assert answer.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert_nothing_removed(shelf)


# 27
def test_no_log_record_and_no_answer_carries_the_stored_access_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    shelf = stocked()
    client = an_admin_over(shelf.decks).client
    caplog.set_level(logging.DEBUG)
    answers = [preview_removal(client, shelf.name)]
    seen = shelf.preview()
    answers.append(remove(client, shelf.name, deck_count=seen[0] + 1, run_count=0))
    shelf.folders.stuck = frozenset({shelf.going.id})
    answers.append(remove(client, shelf.name, deck_count=seen[0], run_count=seen[1]))
    shelf.folders.stuck = frozenset()
    answers.append(remove(client, shelf.name, deck_count=seen[0], run_count=seen[1]))
    answers.append(remove(client, shelf.name, deck_count=seen[0], run_count=seen[1]))

    assert [each.status_code for each in answers] == [
        HTTPStatus.OK,
        HTTPStatus.CONFLICT,
        HTTPStatus.INTERNAL_SERVER_ERROR,
        HTTPStatus.NO_CONTENT,
        HTTPStatus.NOT_FOUND,
    ]
    assert caplog.records
    logged = "\n".join(logging.Formatter().format(each) for each in caplog.records)
    assert shelf.credential not in logged
    for answer in answers:
        assert_nothing_returned(answer, shelf.credential)


# 30
def test_the_generated_spec_types_both_routes_and_their_problems() -> None:
    spec = an_admin_over(decks_that_must_not_run()).app.openapi()
    previewing = spec["paths"][REMOVAL]["get"]
    removing = spec["paths"][PAGE]["delete"]

    assert previewing["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SourceRemovalPreview"
    }
    assert "requestBody" not in removing
    counts = {
        each["name"]: each for each in removing["parameters"] if each["in"] == "query"
    }
    assert set(counts) == {"deck_count", "run_count"}
    for each in counts.values():
        assert each["required"] is True
        assert each["schema"]["type"] == "integer"
        assert each["schema"]["minimum"] == 0
    assert "204" in removing["responses"]
    assert "422" in removing["responses"]
    for route in (previewing, removing):
        for refused in ("403", "404"):
            assert set(route["responses"][refused]["content"]) == {PROBLEM_MEDIA_TYPE}
    for status, problem in (
        ("409", SourceRemovalStaleProblem(deck_count=0, run_count=0)),
        ("500", SourceRemovalRefusedProblem(reason=SourceRemovalRefusal.DISK_LEFTOVER)),
    ):
        schema = removing["responses"][status]["content"][PROBLEM_MEDIA_TYPE]["schema"]
        assert schema["properties"]["type"]["const"] == problem.type
