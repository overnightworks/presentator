"""Two app processes on one Postgres keep the guarantees of every shared write.

Each case sends the same kind of request to both processes at once, as a load
balancer does, and judges the one database and what either process answers.
"""

from collections import defaultdict
from collections.abc import Iterator
from http import HTTPStatus
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from presentator.application.decks import SourceRefusal
from presentator.contracts.decks import RECENT_SOURCE_RUNS, SourceRunOutcome
from tests.presentator.adapters.support.build_support import (
    A_TOOLCHAIN_THAT_BUILDS,
    with_the_toolchain,
)
from tests.presentator.host.support.app_process import directory_name_of
from tests.presentator.host.support.app_processes import (
    KEY_DRAFT,
    App,
    Browser,
    serving_apps,
)
from tests.presentator.host.support.app_requests import (
    AT,
    added,
    admin_of,
    at_once,
    checked,
    create_source,
    fetched,
    listed_slugs,
    removal_of,
    scalar,
    scalar_text,
    texts,
    watching,
)
from tests.support.git_remote import EXAMPLE_SLUG, GitRemote, with_the_program
from tests.support.object_store import ObjectStore
from tests.support.postgres import engine_on

_SETTINGS = "/api/settings"
_ACCOUNT = "/api/account"
_ROUNDS = RECENT_SOURCE_RUNS + 1
# The loser of two creations of one source is told the name or the address is
# taken, by the pre-check when the winner was already stored, else by the store.
_TAKEN = {SourceRefusal.DUPLICATE_NAME, SourceRefusal.DUPLICATE_URL}
_NOT_A_DRAFT = "not-a-draft"
_MARKING_ROUNDS = 12
_MARKED_DECKS = 12
_REBUILDS = 4
_OPENINGS = 80
_DRAFT_IDS = "SELECT id FROM source_key_drafts ORDER BY id"
_BUILD_OF_THE_DECK = (
    "SELECT built_run, active_build, pdf_export, built_commit FROM decks"
)
# Stands in for the ssh git starts: it runs the upload-pack the remote command
# names on this machine, so an scp-form address reaches a repository on disk.
_SSH_ON_THIS_MACHINE = """#!/bin/sh
for command; do :; done
exec sh -c "git ${command#git-}"
"""
# Stands in for the ssh git starts, too, but reaches the repository its caller's
# route names: git tells ssh the known_hosts file of the app it runs for, and
# that file lies in the directory of that app alone. Where the route names a
# host key too, the host answers with it and is checked as `accept-new` does.
_SSH_TO_THE_ROUTE_OF_THE_CALLER = """#!/bin/sh
for argument; do
    case "$argument" in UserKnownHostsFile=*) hosts=${{argument#*=}};; esac
done
caller=$(basename "$(dirname "$(dirname "$hosts")")")
if [ -f "{routes}/$caller.host-key" ]; then
    presented=$(cat "{routes}/$caller.host-key")
    pinned=$(sed -n "s/^{host} //p" "$hosts")
    if [ -n "$pinned" ] && [ "$pinned" != "$presented" ]; then
        echo "Host key verification failed." >&2
        exit 255
    fi
    [ -n "$pinned" ] || printf '%s %s\\n' "{host}" "$presented" >> "$hosts"
fi
exec git upload-pack "$(cat "{routes}/$caller")"
"""
_SSH_HOST = "localhost"
_SSH_ADDRESS = f"git@{_SSH_HOST}:{{repository}}"
_THE_HOST_KEY = "ssh-ed25519 AAAAthe-host"
_A_CHANGED_HOST_KEY = "ssh-ed25519 AAAAa-changed-host"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    each = engine_on(database_url)
    try:
        yield each
    finally:
        each.dispose()


@pytest.fixture
def committed_remotes(
    remote: GitRemote, another_remote: GitRemote
) -> tuple[GitRemote, GitRemote]:
    """Two repositories that each carry a commit."""
    for each in (remote, another_remote):
        each.commit({"README.md": "talks"}, at=AT)
    return remote, another_remote


@pytest.fixture
def remotes_over_ssh(
    environment: pytest.MonkeyPatch,
    tmp_path: Path,
    committed_remotes: tuple[GitRemote, GitRemote],
) -> tuple[GitRemote, GitRemote]:
    """Two repositories git reaches through an ssh that runs on this machine."""
    with_the_program(environment, tmp_path, "ssh", _SSH_ON_THIS_MACHINE)
    return committed_remotes


@pytest.fixture
def routes(environment: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The directory naming, per app, the repository its ssh reaches; builds succeed."""
    directory = tmp_path / "routes"
    directory.mkdir()
    with_the_program(
        environment,
        tmp_path,
        "ssh",
        _SSH_TO_THE_ROUTE_OF_THE_CALLER.format(routes=directory, host=_SSH_HOST),
    )
    with_the_toolchain(environment, tmp_path, A_TOOLCHAIN_THAT_BUILDS)
    return directory


def _route(routes: Path, app: App, remote: GitRemote) -> None:
    """Let the ssh that `app` runs reach `remote`, whatever address it is given."""
    (routes / directory_name_of(app.port)).write_text(
        str(remote.bare), encoding="utf-8"
    )


def _presenting(routes: Path, app: App, host_key: str) -> None:
    """Let the host the ssh of `app` reaches answer with that key."""
    (routes / f"{directory_name_of(app.port)}.host-key").write_text(
        host_key, encoding="utf-8"
    )


def _opened_draft(admin: Browser, app: App) -> dict[str, str]:
    answer = admin.request(app, "POST", KEY_DRAFT)
    assert answer.status_code == HTTPStatus.OK, answer.text
    return answer.json()


def _one_created_one_refused(
    answers: list[httpx2.Response], *, refused_as: set[SourceRefusal]
) -> None:
    assert sorted(each.status_code for each in answers) == [
        HTTPStatus.CREATED,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ], [each.text for each in answers]
    refused = [each.json()["reason"] for each in answers if each.is_client_error]
    assert len(refused) == 1
    assert refused[0] in refused_as


def test_a_failed_read_while_watching_fails_the_test(engine: Engine) -> None:
    with (
        pytest.raises(SQLAlchemyError),
        watching(engine, "SELECT 1 FROM a_table_that_does_not_exist"),
    ):
        pass


@pytest.mark.usefixtures("environment")
@pytest.mark.parametrize(
    "second",
    [("talks", 0), ("talks", 1), ("other-talks", 0)],
    ids=["one name and address", "one name", "one address"],
)
def test_a_source_created_at_both_processes_at_once_is_stored_once(
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    committed_remotes: tuple[GitRemote, GitRemote],
    second: tuple[str, int],
) -> None:
    second_name, second_remote = second
    with serving_apps(database_url, tmp_path) as apps:
        admin = admin_of(apps[0])
        first = checked(admin, apps[0], name="talks", url=committed_remotes[0].url)
        second_body = checked(
            admin, apps[0], name=second_name, url=committed_remotes[second_remote].url
        )

        answers = at_once(
            [
                lambda: create_source(admin, apps[0], first),
                lambda: create_source(admin, apps[1], second_body),
            ]
        )

    _one_created_one_refused(answers, refused_as=_TAKEN)
    assert scalar(engine, "SELECT count(*) FROM sources") == 1


@pytest.mark.usefixtures("environment")
def test_a_key_draft_promoted_at_both_processes_at_once_makes_one_source(
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    remotes_over_ssh: tuple[GitRemote, GitRemote],
) -> None:
    with serving_apps(database_url, tmp_path) as apps:
        admin = admin_of(apps[0])
        draft = _opened_draft(admin, apps[0])
        bodies = [
            checked(
                admin,
                apps[0],
                name=name,
                url=_SSH_ADDRESS.format(repository=each.bare),
                key_draft_id=draft["id"],
            )
            for name, each in zip(("first", "second"), remotes_over_ssh, strict=True)
        ]

        answers = at_once(
            [
                lambda: create_source(admin, apps[0], bodies[0]),
                lambda: create_source(admin, apps[1], bodies[1]),
            ]
        )

    # A loser that reads the draft after the winner consumed it hears its key is
    # unavailable and is given a replacement, so only the original key is judged.
    _one_created_one_refused(
        answers, refused_as={*_TAKEN, SourceRefusal.DEPLOY_KEY_UNAVAILABLE}
    )
    key = draft["public_key"]
    assert scalar(engine, "SELECT count(*) FROM sources") == 1
    assert (
        scalar(
            engine, "SELECT count(*) FROM sources WHERE ssh_public_key = :key", key=key
        )
        == 1
    )
    assert (
        scalar(
            engine,
            "SELECT count(*) FROM source_key_drafts WHERE public_key = :key",
            key=key,
        )
        == 0
    )


def _create_with_a_stale_draft(admin: Browser, app: App) -> httpx2.Response:
    """A creation whose draft is not the admin's current one, which replaces it."""
    return create_source(
        admin,
        app,
        {
            "name": "talks",
            "url": _SSH_ADDRESS.format(repository="/nowhere.git"),
            "secret": None,
            "key_draft_id": _NOT_A_DRAFT,
            "fingerprint": "unchecked",
        },
    )


def _only_draft(engine: Engine) -> str:
    assert scalar(engine, "SELECT count(*) FROM source_key_drafts") == 1
    return scalar_text(engine, "SELECT id FROM source_key_drafts")


@pytest.mark.usefixtures("environment")
def test_a_key_draft_replaced_at_both_processes_at_once_leaves_one_new_draft(
    database_url: str, tmp_path: Path, engine: Engine
) -> None:
    with serving_apps(database_url, tmp_path) as apps:
        admin = admin_of(apps[0])
        original = _opened_draft(admin, apps[0])["id"]

        answers = at_once(
            [lambda app=app: _create_with_a_stale_draft(admin, app) for app in apps]
        )

        reopened = [_opened_draft(admin, app)["id"] for app in apps]

    assert [each.json()["reason"] for each in answers] == [
        SourceRefusal.DEPLOY_KEY_UNAVAILABLE
    ] * 2
    kept = _only_draft(engine)
    assert kept != original
    assert reopened == [kept, kept]


def _opened_beside_a_replacement(
    admin: Browser, opening: App, replacing: App
) -> list[httpx2.Response]:
    return at_once(
        [
            lambda: admin.request(opening, "POST", KEY_DRAFT),
            lambda: _create_with_a_stale_draft(admin, replacing),
        ]
    )


@pytest.mark.usefixtures("environment")
def test_a_key_draft_opened_beside_its_replacement_never_overwrites_it(
    database_url: str, tmp_path: Path, engine: Engine
) -> None:
    with serving_apps(database_url, tmp_path) as apps:
        admin = admin_of(apps[0])
        for round_number in range(_OPENINGS):
            # No draft, so the opening reads none and then writes.
            with engine.begin() as connection:
                connection.execute(text("DELETE FROM source_key_drafts"))
            opening, replacing = apps if round_number % 2 == 0 else (apps[1], apps[0])

            with watching(engine, _DRAFT_IDS) as seen:
                opened, replaced = _opened_beside_a_replacement(
                    admin, opening, replacing
                )
                reopened = [_opened_draft(admin, app)["id"] for app in apps]

            assert replaced.json()["reason"] == SourceRefusal.DEPLOY_KEY_UNAVAILABLE
            kept = _only_draft(engine)
            assert reopened == [kept, kept]
            # An opening that answers with the draft the database ends with has
            # either found the replacement or been the only one ever stored;
            # having stored its own after the replacement is the overwrite.
            if opened.json()["id"] == kept:
                stored = {each[0] for observation in seen for each in observation}
                assert stored == {kept}


@pytest.mark.usefixtures("environment")
def test_runs_recorded_at_both_processes_at_once_keep_exactly_the_bound(
    database_url: str, tmp_path: Path, engine: Engine, remote: GitRemote
) -> None:
    remote.commit({"README.md": "talks"}, at=AT)
    with serving_apps(database_url, tmp_path) as apps:
        admin = admin_of(apps[0])
        added(admin, apps[0], "talks", remote)

        for _ in range(_ROUNDS):
            answers = at_once(
                [lambda app=app: fetched(admin, app, "talks") for app in apps]
            )
            # One process refreshes at a time: the other answers without a run.
            assert any(each.json()["run"] is not None for each in answers)

    assert scalar(engine, "SELECT count(*) FROM source_runs") == RECENT_SOURCE_RUNS
    assert _ROUNDS * len(apps) > RECENT_SOURCE_RUNS


@pytest.mark.usefixtures("environment")
def test_two_sources_carrying_one_deck_fetched_at_both_processes_give_it_to_one(
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    remote: GitRemote,
    another_remote: GitRemote,
) -> None:
    for each in (remote, another_remote):
        each.commit_example_deck(at=AT)
    carrier = "SELECT s.name FROM decks d JOIN sources s ON s.id = d.source_id"
    with serving_apps(database_url, tmp_path) as apps:
        admin = admin_of(apps[0])
        added(admin, apps[0], "first", remote)
        added(admin, apps[0], "second", another_remote)

        at_once(
            [
                lambda: fetched(admin, apps[0], "first"),
                lambda: fetched(admin, apps[1], "second"),
            ]
        )
        keeper = scalar_text(engine, carrier)
        listed = [listed_slugs(admin, app) for app in apps]
        fetched(admin, apps[1], ({"first", "second"} - {keeper}).pop())

    assert scalar(engine, "SELECT count(*) FROM decks") == 1
    assert listed == [[EXAMPLE_SLUG], [EXAMPLE_SLUG]]
    assert scalar_text(engine, carrier) == keeper


@pytest.mark.usefixtures("environment")
def test_a_deck_removed_upstream_is_marked_by_fetches_at_both_processes(
    database_url: str, tmp_path: Path, engine: Engine, remote: GitRemote
) -> None:
    remote.commit_example_deck(at=AT)
    with serving_apps(database_url, tmp_path) as apps:
        admin = admin_of(apps[0])
        added(admin, apps[0], "talks", remote)
        fetched(admin, apps[0], "talks")
        assert listed_slugs(admin, apps[1]) == [EXAMPLE_SLUG]
        remote.remove(EXAMPLE_SLUG, at=AT)

        at_once([lambda app=app: fetched(admin, app, "talks") for app in apps])

        listed = [listed_slugs(admin, app) for app in apps]

    assert listed == [[], []]
    assert scalar(engine, "SELECT count(*) FROM decks WHERE removed_at IS NULL") == 0
    assert scalar(engine, "SELECT count(*) FROM decks") == 1


@pytest.mark.usefixtures("environment")
def test_fetches_of_trees_missing_each_others_decks_mark_what_one_fetch_would(
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    routes: Path,
    committed_remotes: tuple[GitRemote, GitRemote],
) -> None:
    first_tree, second_tree = committed_remotes
    decks_of = (
        [f"first-{number}" for number in range(_MARKED_DECKS)],
        [f"second-{number}" for number in range(_MARKED_DECKS)],
    )
    for tree, slugs in zip(committed_remotes, decks_of, strict=True):
        tree.commit_example_decks(at=AT, into=slugs)
    with serving_apps(database_url, tmp_path) as apps:
        admin = admin_of(apps[0])
        draft = _opened_draft(admin, apps[0])
        _route(routes, apps[0], first_tree)
        created = create_source(
            admin,
            apps[0],
            checked(
                admin,
                apps[0],
                name="talks",
                url=_SSH_ADDRESS.format(repository=first_tree.bare),
                key_draft_id=draft["id"],
            ),
        )
        assert created.status_code == HTTPStatus.CREATED, created.text
        fetched(admin, apps[0], "talks")
        _route(routes, apps[0], second_tree)
        fetched(admin, apps[0], "talks")
        _route(routes, apps[0], first_tree)
        _route(routes, apps[1], second_tree)

        outcomes: list[str] = []
        visible_after: list[set[str]] = []
        for _ in range(_MARKING_ROUNDS):
            answers = at_once(
                [lambda app=app: fetched(admin, app, "talks") for app in apps]
            )
            outcomes += [
                each.json()["run"]["outcome"]
                for each in answers
                if each.json()["run"] is not None
            ]
            visible_after.append(
                texts(engine, "SELECT slug FROM decks WHERE removed_at IS NULL")
            )

    assert set(outcomes) == {SourceRunOutcome.SUCCESS}
    assert len(outcomes) >= _MARKING_ROUNDS
    assert scalar(engine, "SELECT count(*) FROM decks") == 2 * _MARKED_DECKS
    assert all(each in (set(decks_of[0]), set(decks_of[1])) for each in visible_after)


def test_a_deck_rebuilt_at_both_processes_at_once_is_delivered_from_one_build(  # noqa: PLR0913 - the store is one more part of the scene
    environment: pytest.MonkeyPatch,
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    remote: GitRemote,
    *,
    object_store: ObjectStore,
) -> None:
    with_the_toolchain(environment, tmp_path, A_TOOLCHAIN_THAT_BUILDS)
    remote.commit_example_deck(at=AT)
    pushed = {remote.head}
    with serving_apps(database_url, tmp_path, object_store.environment) as apps:
        admin = admin_of(apps[0])
        added(admin, apps[0], "talks", remote)

        with watching(engine, _BUILD_OF_THE_DECK) as seen:
            for push in range(_REBUILDS):
                remote.commit({f"{EXAMPLE_SLUG}/notes.md": f"push {push}"}, at=AT)
                pushed.add(remote.head)
                at_once([lambda app=app: fetched(admin, app, "talks") for app in apps])

    commits_of_a_run = defaultdict[str, set[str]](set)
    for observation in seen:
        for run, talk, pdf, commit in observation:
            if run is None and talk is None and pdf is None and commit is None:
                continue
            assert run is not None
            assert talk is not None
            assert pdf is not None
            assert Path(talk).parent == Path(pdf).parent
            assert Path(talk).parent.parent.name == run
            assert commit is not None
            assert commit in pushed
            commits_of_a_run[run].add(commit)
    assert all(len(commits) == 1 for commits in commits_of_a_run.values())
    assert seen[-1][0][3] == remote.head


@pytest.mark.usefixtures("environment")
def test_settings_and_a_theme_saved_at_both_processes_at_once_leave_one_row_each(
    database_url: str, tmp_path: Path, engine: Engine
) -> None:
    names = ["Atelier North", "Atelier South"]
    themes = ["light", "dark"]
    with serving_apps(database_url, tmp_path) as apps:
        admin = admin_of(apps[0])

        answers = at_once(
            [
                lambda: admin.request(
                    apps[0], "PUT", _SETTINGS, body={"name": names[0]}
                ),
                lambda: admin.request(
                    apps[1], "PUT", _SETTINGS, body={"name": names[1]}
                ),
                lambda: admin.request(
                    apps[0], "PUT", _ACCOUNT, body={"theme": themes[0]}
                ),
                lambda: admin.request(
                    apps[1], "PUT", _ACCOUNT, body={"theme": themes[1]}
                ),
            ]
        )

        read_names = [
            admin.request(app, "GET", _SETTINGS).json()["name"] for app in apps
        ]
        read_themes = [
            admin.request(app, "GET", _ACCOUNT).json()["theme"] for app in apps
        ]

    assert [each.status_code for each in answers] == [HTTPStatus.OK] * 4
    assert scalar(engine, "SELECT count(*) FROM instance_settings") == 1
    assert scalar(engine, "SELECT count(*) FROM person_preferences") == 1
    assert read_names[0] == read_names[1]
    assert read_names[0] in names
    assert read_themes[0] == read_themes[1]
    assert read_themes[0] in themes


def _added_over_ssh(admin: Browser, app: App, name: str, remote: GitRemote) -> None:
    """Check and store a source with a deploy key, reached over ssh at that app."""
    draft = _opened_draft(admin, app)
    created = create_source(
        admin,
        app,
        checked(
            admin,
            app,
            name=name,
            url=_SSH_ADDRESS.format(repository=remote.bare),
            key_draft_id=draft["id"],
        ),
    )
    assert created.status_code == HTTPStatus.CREATED, created.text


@pytest.mark.usefixtures("environment")
def test_a_host_key_pinned_at_one_process_refuses_a_changed_key_at_the_other(
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    routes: Path,
    remote: GitRemote,
) -> None:
    """SR12: the key first met at one process is the key every process trusts."""
    remote.commit_example_deck(at=AT)
    with serving_apps(database_url, tmp_path) as apps:
        admin = admin_of(apps[0])
        for app in apps:
            _route(routes, app, remote)
        _presenting(routes, apps[0], _THE_HOST_KEY)
        _added_over_ssh(admin, apps[0], "talks", remote)
        _presenting(routes, apps[1], _A_CHANGED_HOST_KEY)
        changed = fetched(admin, apps[1], "talks").json()["run"]
        _presenting(routes, apps[1], _THE_HOST_KEY)
        pinned = fetched(admin, apps[1], "talks").json()["run"]

    assert changed["outcome"] == SourceRunOutcome.FAILURE
    assert pinned["outcome"] == SourceRunOutcome.SUCCESS
    assert texts(engine, "SELECT host || ' ' || public_key FROM host_key_pins") == {
        f"{_SSH_HOST} {_THE_HOST_KEY}"
    }


def _mirrors_of(tmp_path: Path, app: App) -> set[str]:
    """The names in the mirrors directory of that app's own disk."""
    directory = tmp_path / directory_name_of(app.port) / "mirrors"
    return {each.name for each in directory.iterdir() if each.is_dir()}


@pytest.mark.usefixtures("environment")
def test_a_mirror_of_a_source_removed_at_one_process_goes_at_the_others_next_turn(
    database_url: str,
    tmp_path: Path,
    committed_remotes: tuple[GitRemote, GitRemote],
) -> None:
    """SR13: another process's mirror of a removed source goes at its next turn."""
    talks, other_talks = committed_remotes
    with serving_apps(database_url, tmp_path) as apps:
        admin = admin_of(apps[0])
        added(admin, apps[0], "talks", talks)
        added(admin, apps[0], "other-talks", other_talks)
        fetched(admin, apps[1], "talks")
        before = _mirrors_of(tmp_path, apps[1])
        removed = removal_of(admin, apps[0], "talks")
        fetched(admin, apps[1], "other-talks")
        after = _mirrors_of(tmp_path, apps[1])

    assert removed == HTTPStatus.NO_CONTENT
    assert len(before) == 1
    assert len(after) == 1
    assert not before & after
