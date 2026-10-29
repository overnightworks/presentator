"""The composition root, from the environment to a lobby that answers."""

import asyncio
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import SecretStr
from starlette.routing import Router
from webauth.config import installed_web_auth_config
from webauth.cookies import DEFAULT_SESSION_COOKIE_NAME

from presentator.adapters.deck_store import SqliteDeckStore
from presentator.adapters.decks import SqliteSourceStore
from presentator.adapters.identity import (
    SqliteSessionRecordStore,
    TokenIdentifierFactory,
    create_identity_tables,
)
from presentator.adapters.secrets import secret_box
from presentator.adapters.sqlite import rows
from presentator.api.hooks import hook_address
from presentator.application.decks import hash_webhook_secret
from presentator.application.identity import SESSION_LIFETIME
from presentator.contracts.decks import SourceWrite
from presentator.contracts.models import Role, User
from presentator.host import main
from presentator.host.config import (
    SECRET_LENGTH,
    ConfigurationError,
    WhereBuildsRun,
    load_settings,
)
from presentator.host.main import Instance
from tests.support.git_remote import (
    EXAMPLE_SLUG,
    EXAMPLE_TITLE,
    GitRemote,
)
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_BROWSERS_HEADERS,
    INSTANCE_KEY,
    configure_secret_key,
    hand_over_session_cookies,
)
from tests.support.people import (
    copy_of_a_crashed_database,
    seed_user,
    write_password_schema,
)

_PERSON = "felix"
_ADMIN = User(id="id-of-felix", username=_PERSON, role=Role.ADMIN)
_PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)
_DELETED_AT = datetime(2026, 1, 16, 9, tzinfo=UTC)
_PUSHED_AGAIN_AT = datetime(2026, 2, 1, 9, tzinfo=UTC)
_ANOTHER_SLUG = "kundenfeedback"
_SOURCE_NAME = "talks"
_ANOTHER_SOURCE_NAME = "more-talks"
_WHAT_THE_HOST_CARRIES = "the words only this source's host was given"
_WHAT_THE_GIT_HOST_EXPECTS = "the read-only words only this test made up"
_CREDENTIAL_VARIABLE = "A_READ_ONLY_TOKEN"
_ANOTHER_INSTANCE_KEY = "the key another instance carries"


def a_real_instance() -> Instance:
    """The whole stack the composition root builds, on the configured database."""
    return main.build_instance(load_settings())


def signed_in(
    instance: Instance,
    *,
    base_url: str = A_BROWSERS_ADDRESS,
    peer: str = "testclient",
) -> TestClient:
    """A browser at that stack, signed in as the admin the database carries."""
    database = load_settings().database
    seed_user(database, _ADMIN)
    now = datetime.now(UTC)
    session = SqliteSessionRecordStore(database).create(
        _ADMIN.id,
        now=now,
        expires_at=now + SESSION_LIFETIME,
        refresh_token=None,
        id_token=SecretStr("an-id-token"),
    )
    client = TestClient(
        instance.lobby,
        base_url=base_url,
        follow_redirects=False,
        client=(peer, 50000),
        headers=A_BROWSERS_HEADERS,
    )
    hand_over_session_cookies(
        client,
        session.id,
        installed_web_auth_config(instance.lobby),
    )
    return client


def test_the_composition_root_serves_a_lobby_that_answers_the_login(
    environment: pytest.MonkeyPatch,
) -> None:
    served: list[Router] = []

    class ServerThatOnlyRecordsWhatItGot:
        def __init__(self, config: uvicorn.Config) -> None:
            assert isinstance(config.app, Router)
            self.app = config.app

        async def serve(self) -> None:
            served.append(self.app)

    environment.setattr(main.uvicorn, "Server", ServerThatOnlyRecordsWhatItGot)

    main.main()

    assert len(served) == 1
    assert TestClient(served[0], base_url=A_BROWSERS_ADDRESS).get("/login").is_success


def test_a_trusted_proxy_list_that_is_not_addresses_refuses_to_start(
    environment: pytest.MonkeyPatch,
) -> None:
    environment.setenv("PRESENTATOR_TRUSTED_PROXIES", "not-an-address")

    with pytest.raises(ConfigurationError, match="trusted_proxies"):
        load_settings()


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "http://presentator.test",
        "https://user@presentator.test",
        "https://presentator.test/",
        "https://presentator.test/path",
        "https://presentator.test?query=yes",
        "https://presentator.test#fragment",
        "https://presentator.test:not-a-port",
    ],
)
def test_the_public_origin_must_be_one_canonical_https_origin(
    environment: pytest.MonkeyPatch,
    origin: str,
) -> None:
    environment.setenv("PRESENTATOR_PUBLIC_ORIGIN", origin)

    with pytest.raises(ConfigurationError, match="public_origin"):
        load_settings()


def test_an_instance_that_says_nothing_about_where_builds_run_builds_in_the_worker(
    environment: pytest.MonkeyPatch,
) -> None:
    """Cell B26: an unset runner builds in the worker."""
    environment.delenv("PRESENTATOR_BUILD_RUNNER")

    assert load_settings().build_runner is WhereBuildsRun.WORKER


@pytest.mark.parametrize(
    ("given", "runs"),
    [("worker", WhereBuildsRun.WORKER), ("host", WhereBuildsRun.HOST)],
)
def test_a_runner_an_instance_names_is_the_one_it_builds_with(
    environment: pytest.MonkeyPatch,
    given: str,
    runs: WhereBuildsRun,
) -> None:
    """Cell B26: the worker and the host are the runners an instance may name."""
    environment.setenv("PRESENTATOR_BUILD_RUNNER", given)

    assert load_settings().build_runner is runs


def test_a_runner_that_is_no_longer_one_refuses_to_start(
    environment: pytest.MonkeyPatch,
) -> None:
    """Cell B26: the removed `container` runner is refused at start."""
    environment.setenv("PRESENTATOR_BUILD_RUNNER", "container")

    with pytest.raises(ConfigurationError, match="build_runner"):
        load_settings()


def test_a_removed_build_key_in_the_process_environment_is_ignored(
    environment: pytest.MonkeyPatch,
) -> None:
    """Cell B18: a removed build key in the process environment is ignored."""
    environment.setenv("PRESENTATOR_BUILD_IMAGE", "left-over-from-the-container-runner")

    assert load_settings().build_runner is WhereBuildsRun.HOST


def test_a_removed_build_key_in_the_env_file_of_a_direct_run_refuses_to_start(
    environment: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cell B18: a removed build key in the `.env` file refuses to start."""
    (tmp_path / ".env").write_text(
        "PRESENTATOR_BUILD_IMAGE=left-over-from-the-container-runner\n",
        encoding="utf-8",
    )
    environment.chdir(tmp_path)

    with pytest.raises(ConfigurationError, match="presentator_build_image"):
        load_settings()


@pytest.mark.usefixtures("environment")
def test_the_real_stack_keeps_a_seeded_person_in_until_they_sign_out() -> None:
    real_lobby = signed_in(a_real_instance())
    assert _PERSON in real_lobby.get("/").text

    signed_in_cookie = real_lobby.cookies[DEFAULT_SESSION_COOKIE_NAME]
    assert real_lobby.post("/logout").status_code == HTTPStatus.OK

    real_lobby.cookies.set(DEFAULT_SESSION_COOKIE_NAME, signed_in_cookie)
    assert real_lobby.get("/").status_code == HTTPStatus.FOUND


@pytest.mark.usefixtures("environment")
def test_the_real_stack_issues_no_cookie_with_an_ordinary_answer() -> None:
    answered = signed_in(a_real_instance()).get("/")

    assert answered.is_success
    assert answered.headers.get_list("set-cookie") == []


@pytest.mark.parametrize(
    ("trusted_proxies", "hook_scheme"),
    [("127.0.0.1", "https"), ("", "http")],
    ids=["trusted-peer", "no-trusted-peer"],
)
def test_the_hook_address_names_https_only_behind_a_trusted_proxy(
    environment: pytest.MonkeyPatch,
    remote: GitRemote,
    tmp_path: Path,
    *,
    trusted_proxies: str,
    hook_scheme: str,
) -> None:
    environment.setenv("PRESENTATOR_TRUSTED_PROXIES", trusted_proxies)
    instance = a_polled_source(remote, tmp_path)
    browser = signed_in(instance, peer="127.0.0.1")
    # A browser sends a Secure cookie over https only, so the cookie rides as a
    # header to reach the lobby the way a proxy that terminated TLS would.
    cookie = "; ".join(f"{name}={value}" for name, value in browser.cookies.items())

    page = browser.get(
        f"http://presentator.test/settings/sources/{_SOURCE_NAME}",
        headers={"x-forwarded-proto": "https", "cookie": cookie},
    )

    assert f"{hook_scheme}://presentator.test{hook_address(_SOURCE_NAME)}" in page.text


@pytest.mark.usefixtures("environment")
def test_the_real_stack_offers_no_password_login_and_no_first_start() -> None:
    real_lobby = signed_in(a_real_instance())

    password_posted = real_lobby.post(
        "/login",
        data={"username": _PERSON, "password": "the words only this test types"},
    )

    assert password_posted.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert real_lobby.get("/setup").status_code == HTTPStatus.NOT_FOUND
    assert 'type="password"' not in real_lobby.get("/login").text


@pytest.mark.usefixtures("bare_environment")
def test_an_instance_without_a_key_file_refuses_to_start() -> None:
    with pytest.raises(ConfigurationError, match="secret_key_file"):
        load_settings()


def test_the_old_key_variable_alone_no_longer_starts_an_instance(
    bare_environment: pytest.MonkeyPatch,
) -> None:
    """#509 matrix: the old environment variable alone no longer starts the web."""
    bare_environment.setenv("PRESENTATOR_SECRET_KEY", INSTANCE_KEY)

    with pytest.raises(ConfigurationError, match="secret_key_file"):
        load_settings()


def test_a_relative_key_file_refuses_to_start(
    bare_environment: pytest.MonkeyPatch,
) -> None:
    """#509 acceptance 1: a relative key file path refuses by secret_key_file."""
    bare_environment.setenv("PRESENTATOR_SECRET_KEY_FILE", "secret-key")

    with pytest.raises(ConfigurationError, match="secret_key_file"):
        load_settings()


@pytest.mark.parametrize(
    ("content", "is_directory"),
    [
        (None, False),
        (None, True),
        (b"", False),
        (b"\n", False),
        (b"the words only this test types", False),
        (b"k" * (SECRET_LENGTH - 1), False),
    ],
    ids=["missing", "a directory", "empty", "a newline", "too short", "one short"],
)
def test_a_key_file_nobody_can_use_refuses_naming_the_setting_and_not_its_content(
    bare_environment: pytest.MonkeyPatch,
    tmp_path: Path,
    content: bytes | None,
    *,
    is_directory: bool,
) -> None:
    """#509 acceptance 1: an unusable key file refuses and never shows its content."""
    key_file = tmp_path / "unusable-key"
    if is_directory:
        key_file.mkdir()
    elif content is not None:
        key_file.write_bytes(content)
    bare_environment.setenv("PRESENTATOR_SECRET_KEY_FILE", str(key_file))

    with pytest.raises(ConfigurationError, match="secret_key_file") as refused:
        load_settings()

    assert not content or content.decode() not in str(refused.value)
    assert str(key_file) not in str(refused.value)


@pytest.mark.parametrize("trailing", ["", "\n"], ids=["none", "one newline"])
def test_a_key_file_of_thirty_two_characters_starts_with_or_without_a_newline(
    bare_environment: pytest.MonkeyPatch, tmp_path: Path, trailing: str
) -> None:
    """#509 acceptance 1: a 32-character key file starts, newline or not."""
    key = "k" * SECRET_LENGTH
    (tmp_path / "key").write_text(key + trailing, encoding="utf-8")
    bare_environment.setenv("PRESENTATOR_SECRET_KEY_FILE", str(tmp_path / "key"))

    assert load_settings().secret_key.get_secret_value() == key


@pytest.mark.usefixtures("environment")
def test_the_instance_key_is_never_shown() -> None:
    settings = load_settings()

    assert INSTANCE_KEY not in repr(settings)
    assert settings.secret_key.get_secret_value() == INSTANCE_KEY


def test_the_key_file_is_read_once_at_start(
    bare_environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#509 Change: the key is read from its file once, at start."""
    key_file = configure_secret_key(bare_environment, tmp_path, INSTANCE_KEY)
    settings = load_settings()
    key_file.unlink()

    assert settings.secret_key.get_secret_value() == INSTANCE_KEY


def a_signed_in_instance() -> TestClient:
    """The real stack with its admin created and its session open."""
    return signed_in(a_real_instance())


def a_polled_source(
    remote: GitRemote,
    tmp_path: Path,
    *,
    armed: bool = True,
    name: str = _SOURCE_NAME,
) -> Instance:
    """A real stack with that remote stored as a source after first start."""
    instance = a_real_instance()
    signed_in(instance)
    database = tmp_path / "presentator.sqlite3"
    added = sources_over(database).add(
        SourceWrite(
            name=name,
            url=remote.url,
            ref="main",
            owner_id=_ADMIN.id,
            access_secret=_WHAT_THE_GIT_HOST_EXPECTS,
            hook_secret_hash=hash_webhook_secret(_WHAT_THE_HOST_CARRIES),
        ),
    )
    assert added is not None
    if not armed:
        with rows(database) as cursor:
            cursor.execute(
                "UPDATE sources SET hook_secret_hash = NULL WHERE name = ?",
                (name,),
            )
    return instance


def listed_addresses(page: str) -> list[str]:
    """The deck each row of the list links to, in the order they stand."""
    return re.findall(r'href="/deck/([^"]+)"', page)


def owner_of(database: Path, slug: str) -> str:
    """Who the stored deck belongs to, read straight from the real table."""
    kept = SqliteDeckStore(database=database).all()
    return next(deck.owner_id for deck in kept if deck.slug == slug)


def call_the_hook(lobby: TestClient, *, carrying: str) -> Response:
    return lobby.post(
        hook_address(_SOURCE_NAME),
        headers={"authorization": f"Bearer {carrying}"},
    )


def sources_over(
    database: Path, *, instance_key: str = INSTANCE_KEY
) -> SqliteSourceStore:
    """The real source store over the instance's file, keyed as it says.

    What the instance itself writes is keyed by its own instance key; another
    key stands for a row this installation did not write.
    """
    return SqliteSourceStore(
        database=database,
        identifiers=TokenIdentifierFactory(),
        box=secret_box(instance_key),
    )


@pytest.mark.usefixtures("environment")
def test_the_real_stack_pulls_with_the_secret_its_own_key_can_open(
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    instance = a_polled_source(remote, tmp_path)
    lobby = signed_in(instance)
    asyncio.run(instance.poller.tick())
    database = tmp_path / "presentator.sqlite3"
    stored = sources_over(database).all()[0]
    remote.commit_example_deck(at=_PUSHED_AT)

    sources_over(database, instance_key=_ANOTHER_INSTANCE_KEY).put_credential(
        stored.id,
        _WHAT_THE_GIT_HOST_EXPECTS,
    )
    asyncio.run(instance.poller.tick())
    while_the_row_held_what_this_key_cannot_open = lobby.get("/").text

    sources_over(database).put_credential(stored.id, _WHAT_THE_GIT_HOST_EXPECTS)
    asyncio.run(instance.poller.tick())
    after_this_instance_wrote_it = lobby.get("/").text

    assert EXAMPLE_TITLE not in while_the_row_held_what_this_key_cannot_open
    assert EXAMPLE_TITLE in after_this_instance_wrote_it


@pytest.mark.usefixtures("environment")
def test_a_deck_pushed_after_the_start_is_listed_after_one_tick_and_no_request(
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    instance = a_polled_source(remote, tmp_path)
    lobby = signed_in(instance)
    remote.commit_example_deck(at=_PUSHED_AT)

    before_the_tick = lobby.get("/").text
    asyncio.run(instance.poller.tick())
    after_the_tick = lobby.get("/").text

    assert EXAMPLE_TITLE not in before_the_tick
    assert EXAMPLE_TITLE in after_the_tick
    assert f'href="/deck/{EXAMPLE_SLUG}"' in after_the_tick


@pytest.mark.usefixtures("environment")
def test_the_hook_with_the_sources_secret_lists_a_push_at_once(
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    instance = a_polled_source(remote, tmp_path)
    lobby = signed_in(instance)
    asyncio.run(instance.poller.tick())
    remote.commit_example_deck(at=_PUSHED_AT)

    called = call_the_hook(lobby, carrying=_WHAT_THE_HOST_CARRIES)

    assert called.status_code == HTTPStatus.NO_CONTENT
    assert EXAMPLE_TITLE in lobby.get("/").text


@pytest.mark.usefixtures("environment")
def test_a_hook_call_with_a_wrong_secret_leaves_the_list_as_it_was(
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    instance = a_polled_source(remote, tmp_path)
    lobby = signed_in(instance)
    asyncio.run(instance.poller.tick())
    remote.commit_example_deck(at=_PUSHED_AT)

    refused = call_the_hook(lobby, carrying="guessed")

    assert (refused.status_code, refused.content) == (HTTPStatus.NOT_FOUND, b"")
    assert EXAMPLE_TITLE not in lobby.get("/").text


@pytest.mark.usefixtures("environment")
def test_a_source_without_a_webhook_hash_is_refused_alike_not_sent_to_login(
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    lobby = signed_in(a_polled_source(remote, tmp_path, armed=False))
    remote.commit_example_deck(at=_PUSHED_AT)
    lobby.cookies.clear()

    called = call_the_hook(lobby, carrying=_WHAT_THE_HOST_CARRIES)

    assert (called.status_code, called.content) == (HTTPStatus.NOT_FOUND, b"")
    assert called.headers.get("location") is None
    assert EXAMPLE_TITLE not in signed_in(a_real_instance()).get("/").text


@pytest.mark.usefixtures("environment")
def test_a_deck_the_real_stack_took_in_belongs_to_the_instance_admin(
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    instance = a_polled_source(remote, tmp_path)
    remote.commit_example_deck(at=_PUSHED_AT)
    database = tmp_path / "presentator.sqlite3"

    asyncio.run(instance.poller.tick())

    kept = SqliteDeckStore(database=database).all()
    assert [(deck.slug, deck.owner_id) for deck in kept] == [(EXAMPLE_SLUG, _ADMIN.id)]


def test_a_deck_the_real_stack_cannot_build_keeps_its_page_and_offers_no_view(
    environment: pytest.MonkeyPatch,
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    environment.setenv("PRESENTATOR_TOOLCHAIN", str(tmp_path / "no-toolchain-here"))
    instance = a_polled_source(remote, tmp_path)
    lobby = signed_in(instance)
    remote.commit_example_deck(at=_PUSHED_AT)

    asyncio.run(instance.poller.tick())
    page = lobby.get(f"/deck/{EXAMPLE_SLUG}")

    assert page.status_code == HTTPStatus.OK
    assert EXAMPLE_TITLE in page.text
    assert f"/deck/{EXAMPLE_SLUG}/presenter/" not in page.text


def test_a_tick_whose_fetch_exceeds_its_bound_leaves_the_list_answering(
    environment: pytest.MonkeyPatch,
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    environment.setenv("PRESENTATOR_SOURCE_TIMEOUT_SECONDS", "0")
    instance = a_polled_source(remote, tmp_path)
    lobby = signed_in(instance)
    remote.commit_example_deck(at=_PUSHED_AT)

    asyncio.run(instance.poller.tick())
    listed = lobby.get("/")

    assert listed.status_code == HTTPStatus.OK
    assert EXAMPLE_TITLE not in listed.text
    assert "<table" not in listed.text


@pytest.mark.usefixtures("environment")
def test_a_source_that_has_gone_away_still_leaves_its_decks_listed(
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    instance = a_polled_source(remote, tmp_path)
    lobby = signed_in(instance)
    remote.commit_example_deck(at=_PUSHED_AT)
    asyncio.run(instance.poller.tick())

    shutil.rmtree(remote.bare)
    listed = lobby.get("/")

    assert listed.status_code == HTTPStatus.OK
    assert EXAMPLE_TITLE in listed.text


@pytest.mark.usefixtures("environment")
def test_an_instance_without_a_source_shows_the_empty_list() -> None:
    listed = a_signed_in_instance().get("/")

    assert listed.status_code == HTTPStatus.OK
    assert "<table" not in listed.text


def test_a_configured_source_url_in_the_environment_changes_nothing(
    environment: pytest.MonkeyPatch,
    remote: GitRemote,
) -> None:
    environment.setenv("PRESENTATOR_SOURCE_URL", remote.url)
    environment.setenv("PRESENTATOR_SOURCE_NAME", _SOURCE_NAME)
    environment.setenv("PRESENTATOR_SOURCE_CREDENTIAL", _CREDENTIAL_VARIABLE)
    environment.setenv(_CREDENTIAL_VARIABLE, _WHAT_THE_GIT_HOST_EXPECTS)
    lobby = a_signed_in_instance()

    listed = lobby.get("/")
    sources = lobby.get("/settings/sources")

    assert listed.status_code == HTTPStatus.OK
    assert "<table" not in listed.text
    assert "<table" not in sources.text
    assert remote.url not in sources.text


@pytest.mark.usefixtures("environment")
def test_a_deck_deleted_in_git_leaves_the_lobby_and_returns_as_the_same_deck(
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    instance = a_polled_source(remote, tmp_path)
    lobby = signed_in(instance)
    database = tmp_path / "presentator.sqlite3"
    remote.commit_example_deck(at=_PUSHED_AT)
    remote.commit_example_deck(at=_PUSHED_AT, into=_ANOTHER_SLUG)
    asyncio.run(instance.poller.tick())
    both_pushed = listed_addresses(lobby.get("/").text)
    owner_before_the_delete = owner_of(database, EXAMPLE_SLUG)

    remote.remove(EXAMPLE_SLUG, at=_DELETED_AT)
    asyncio.run(instance.poller.tick())
    after_the_delete = listed_addresses(lobby.get("/").text)

    remote.commit_example_deck(at=_PUSHED_AGAIN_AT)
    asyncio.run(instance.poller.tick())
    after_it_came_back = listed_addresses(lobby.get("/").text)

    assert sorted(both_pushed) == sorted([EXAMPLE_SLUG, _ANOTHER_SLUG])
    assert after_the_delete == [_ANOTHER_SLUG]
    assert after_it_came_back == [EXAMPLE_SLUG, _ANOTHER_SLUG]
    assert owner_of(database, EXAMPLE_SLUG) == owner_before_the_delete


@pytest.mark.usefixtures("environment")
def test_two_sources_each_list_their_own_decks_and_reconcile_alone(
    remote: GitRemote,
    another_remote: GitRemote,
    tmp_path: Path,
) -> None:
    first = a_polled_source(remote, tmp_path)
    lobby = signed_in(first)
    remote.commit_example_deck(at=_PUSHED_AT)
    asyncio.run(first.poller.tick())
    another_remote.commit_example_deck(at=_PUSHED_AT, into=_ANOTHER_SLUG)
    database = tmp_path / "presentator.sqlite3"
    added = sources_over(database).add(
        SourceWrite(
            name=_ANOTHER_SOURCE_NAME,
            url=another_remote.url,
            ref="main",
            owner_id=_ADMIN.id,
            access_secret=_WHAT_THE_GIT_HOST_EXPECTS,
            hook_secret_hash=hash_webhook_secret(_WHAT_THE_HOST_CARRIES),
        ),
    )
    assert added is not None
    both = a_real_instance()

    asyncio.run(both.poller.tick())
    listed_together = listed_addresses(lobby.get("/").text)

    remote.remove(EXAMPLE_SLUG, at=_DELETED_AT)
    asyncio.run(both.poller.tick())
    after_the_delete = listed_addresses(lobby.get("/").text)

    assert sorted(listed_together) == sorted([EXAMPLE_SLUG, _ANOTHER_SLUG])
    assert after_the_delete == [_ANOTHER_SLUG]


def test_an_instance_refuses_a_database_with_login_attempts_before_changing_it(
    environment: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "presentator.sqlite3"
    environment.setenv("PRESENTATOR_DATABASE", str(database))
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE login_attempts (username TEXT)")
        connection.commit()
    before = database.read_bytes()
    folder_before = sorted(tmp_path.iterdir())

    with pytest.raises(ConfigurationError, match="move the database"):
        a_real_instance()

    assert database.read_bytes() == before
    assert sorted(tmp_path.iterdir()) == folder_before


def test_an_instance_on_a_relative_database_path_starts_again_after_its_first_start(
    environment: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment.chdir(tmp_path)
    environment.setenv("PRESENTATOR_DATABASE", "presentator.sqlite3")
    a_real_instance()

    a_real_instance()

    assert (tmp_path / "presentator.sqlite3").is_file()


def test_an_instance_refuses_a_password_schema_only_in_the_write_ahead_log(
    environment: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = copy_of_a_crashed_database(
        tmp_path / "crashed" / "presentator.sqlite3", write_password_schema
    )
    environment.setenv("PRESENTATOR_DATABASE", str(database))
    log = database.with_name(database.name + "-wal")
    main_file_before = database.read_bytes()
    log_before = log.read_bytes()

    with pytest.raises(ConfigurationError, match="move the database"):
        a_real_instance()

    assert database.read_bytes() == main_file_before
    assert log.read_bytes() == log_before


def test_an_instance_restarts_on_a_crash_that_left_only_current_frames_in_the_log(
    environment: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_ids: list[str] = []

    def commit_a_person_and_a_session(database: Path) -> None:
        create_identity_tables(database)
        seed_user(database, _ADMIN)
        now = datetime.now(UTC)
        opened = SqliteSessionRecordStore(database).create(
            _ADMIN.id,
            now=now,
            expires_at=now + SESSION_LIFETIME,
            refresh_token=None,
            id_token=SecretStr("an-id-token"),
        )
        session_ids.append(opened.id)

    database = copy_of_a_crashed_database(
        tmp_path / "crashed" / "presentator.sqlite3", commit_a_person_and_a_session
    )
    environment.setenv("PRESENTATOR_DATABASE", str(database))

    instance = a_real_instance()

    survivor = SqliteSessionRecordStore(database).load(session_ids[0])
    assert survivor is not None
    assert survivor.user == _ADMIN
    lobby = TestClient(
        instance.lobby,
        base_url=A_BROWSERS_ADDRESS,
        follow_redirects=False,
        headers=A_BROWSERS_HEADERS,
    )
    hand_over_session_cookies(
        lobby, session_ids[0], installed_web_auth_config(instance.lobby)
    )
    assert lobby.get("/").status_code == HTTPStatus.OK
