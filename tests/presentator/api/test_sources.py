"""Settings · Sources, driven the way a browser drives them."""

from datetime import timedelta
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from presentator.api.hooks import hook_address
from presentator.api.preferences import ACCOUNT, SETTINGS
from presentator.api.sources import REMOVE, REMOVE_CONFIRMED, SOURCES
from presentator.application.decks import RESERVED_SOURCE_NAME, Decks
from presentator.contracts.decks import (
    MANIFEST_FILE,
    SLIDES_FILE,
    AccessKind,
    Build,
    Deck,
    DeckFolder,
    Source,
    SourceRun,
    SourceRunFailure,
    SourceRunOutcome,
)
from presentator.contracts.models import Role
from tests.presentator.api.support.removal_support import (
    DECK_COUNT_FIELD,
    RUN_COUNT_FIELD,
    ask_removal,
    confirm_removal,
)
from tests.presentator.api.support.source_api_support import (
    HTTPS,
    SENT,
    a_created_source,
)
from tests.presentator.application.support.fakes import (
    LOCAL_MOUNT_EXAMPLE,
    FakeBuildRunner,
    FakeDeckStore,
)
from tests.support.lobby import (
    A_LOBBY_ASSET,
    ADMIN,
    ENGLISH,
    NEIGHBOUR,
    NO_DECKS,
    NOW,
    USERNAME,
    GivenDecks,
    Lobby,
    a_configured_source,
    a_lobby,
    a_signed_in_lobby,
)

_ADDRESS = "git@heimserver:decks.git"
_OTHER_ADDRESS = "https://gitlab.example.invalid/decks.git"
_OTHER_ID = "the-other-source"
_COMMIT = "a3f19c2b8d4e5f60718293a4b5c6d7e8f9012345"
_A_DECK = frozenset({MANIFEST_FILE, SLIDES_FILE})
_FETCH = f"{SOURCES}/{{name}}/fetch"
_NEVER_AN_AGE = "—"
_JSON = {"accept": "application/json"}
_A_BOX_FOLDER: dict[str, object] = {
    "url": f"{LOCAL_MOUNT_EXAMPLE}/talks.git",
    "access": AccessKind.FILE,
    "secret": None,
}


def another_source() -> Source:
    return Source(
        id=_OTHER_ID,
        name="talks",
        url=_OTHER_ADDRESS,
        ref="main",
        owner_id=ADMIN,
    )


def a_folder(name: str = "kundenfeedback") -> DeckFolder:
    return DeckFolder(
        name=name,
        file_names=_A_DECK,
        title="A talk",
        changed_at=NOW - timedelta(minutes=2),
        commit=_COMMIT,
    )


def a_run(
    source_id: str,
    *,
    ago: timedelta,
    outcome: SourceRunOutcome,
    reason: SourceRunFailure | None = None,
) -> SourceRun:
    return SourceRun(
        source_id=source_id,
        at=NOW - ago,
        outcome=outcome,
        commit=None if outcome is SourceRunOutcome.FAILURE else _COMMIT,
        reason=reason,
    )


def an_instance_of_two_people(*, given: GivenDecks = NO_DECKS) -> Lobby:
    lobby = a_lobby(given=given)
    lobby.sign_in()
    return lobby


def signed_in_as(lobby: Lobby, username: str) -> None:
    lobby.client.post("/logout")
    lobby.sign_in(username=username)


def fetch(
    client: TestClient, name: str, *, headers: dict[str, str] | None = None
) -> Response:
    return client.post(_FETCH.format(name=name), headers=headers)


@pytest.fixture
def instance() -> Lobby:
    return an_instance_of_two_people(
        given=GivenDecks(source=a_configured_source(_ADDRESS))
    )


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_person_without_the_admin_role_is_refused_at_sources(
    instance: Lobby, method: str
) -> None:
    signed_in_as(instance, NEIGHBOUR)

    refused = {
        "get": lambda: instance.client.get(SOURCES),
        "post": lambda: fetch(instance.client, "decks"),
    }[method]()

    assert refused.status_code == HTTPStatus.FORBIDDEN


def test_only_an_admin_is_offered_the_settings_section(instance: Lobby) -> None:
    assert ENGLISH.section_settings in instance.client.get(ACCOUNT).text

    signed_in_as(instance, NEIGHBOUR)

    assert ENGLISH.section_settings not in instance.client.get(ACCOUNT).text


def test_the_settings_link_is_current_on_the_sources_address(instance: Lobby) -> None:
    answer = instance.client.get(SOURCES)

    assert answer.status_code == HTTPStatus.OK
    page = answer.text

    assert f'aria-current="page">{ENGLISH.section_settings}<' in page
    assert f'href="{SOURCES}" aria-current="page">{ENGLISH.settings_sources}<' in page
    assert f'href="{SETTINGS}">{ENGLISH.settings_title}<' in page


def test_a_source_with_no_run_reads_never_fetched_with_an_em_dash_for_the_age() -> None:
    page = (
        a_signed_in_lobby(
            GivenDecks(source=a_configured_source(_ADDRESS)),
        )
        .get(SOURCES)
        .text
    )

    assert ENGLISH.source_state_never_fetched in page
    assert _NEVER_AN_AGE in page
    assert 'data-state="never-fetched"' in page
    assert "decks" in page
    assert _ADDRESS in page
    assert ENGLISH.sources_fetch_now in page
    assert ENGLISH.decks_column_state in page
    assert ENGLISH.sources_column_source in page
    assert ENGLISH.sources_column_fetched in page


def test_a_row_carries_the_state_and_the_fetched_age_of_the_newest_run() -> None:
    source = a_configured_source(_ADDRESS)
    page = (
        a_signed_in_lobby(
            GivenDecks(
                source=source,
                runs=(
                    a_run(
                        source.id,
                        ago=timedelta(minutes=3),
                        outcome=SourceRunOutcome.SUCCESS,
                    ),
                ),
            ),
        )
        .get(SOURCES)
        .text
    )

    assert ENGLISH.source_state_reachable in page
    assert 'data-state="reachable"' in page
    assert "3 minutes ago" in page
    assert ENGLISH.source_state_never_fetched not in page


def test_a_failed_newest_run_reads_error_and_never_the_credential_word() -> None:
    source = a_configured_source(_ADDRESS)
    page = (
        a_signed_in_lobby(
            GivenDecks(
                source=source,
                runs=(
                    a_run(
                        source.id,
                        ago=timedelta(hours=2),
                        outcome=SourceRunOutcome.FAILURE,
                        reason=SourceRunFailure.CREDENTIAL_UNRESOLVABLE,
                    ),
                ),
            ),
        )
        .get(SOURCES)
        .text
    )

    assert ENGLISH.source_state_error in page
    assert 'data-state="error"' in page
    assert "2 hours ago" in page
    assert SourceRunFailure.CREDENTIAL_UNRESOLVABLE.value not in page


def test_a_refused_login_reads_refused_rather_than_error() -> None:
    source = a_configured_source(_ADDRESS)
    page = (
        a_signed_in_lobby(
            GivenDecks(
                source=source,
                runs=(
                    a_run(
                        source.id,
                        ago=timedelta(minutes=5),
                        outcome=SourceRunOutcome.FAILURE,
                        reason=SourceRunFailure.REFUSED,
                    ),
                ),
            ),
        )
        .get(SOURCES)
        .text
    )

    assert ENGLISH.source_state_refused in page
    assert 'data-state="refused"' in page
    assert ENGLISH.source_state_error not in page


def test_a_host_that_answered_with_something_else_reads_failed_rather_than_error() -> (
    None
):
    source = a_configured_source(_ADDRESS)
    page = (
        a_signed_in_lobby(
            GivenDecks(
                source=source,
                runs=(
                    a_run(
                        source.id,
                        ago=timedelta(minutes=9),
                        outcome=SourceRunOutcome.FAILURE,
                        reason=SourceRunFailure.FAILED,
                    ),
                ),
            ),
        )
        .get(SOURCES)
        .text
    )

    assert ENGLISH.source_state_failed in page
    assert 'data-state="failed"' in page
    assert ENGLISH.source_state_error not in page


def test_fetch_now_refreshes_that_one_source_and_returns_to_the_list() -> None:
    configured = a_configured_source(_ADDRESS)
    other = another_source()
    lobby = a_signed_in_lobby(
        GivenDecks(
            sources=(configured, other),
            carried={configured.id: (a_folder(),), other.id: (a_folder(),)},
            runs=(
                a_run(
                    other.id,
                    ago=timedelta(minutes=3),
                    outcome=SourceRunOutcome.SUCCESS,
                ),
            ),
        ),
    )

    fetched = fetch(lobby, configured.name)

    assert fetched.status_code == HTTPStatus.SEE_OTHER
    assert fetched.headers["location"] == SOURCES
    page = lobby.get(SOURCES).text
    reachable_at = page.index(ENGLISH.source_state_reachable)
    never_at = page.index("3 minutes ago")
    assert page.index("decks") < page.index("talks")
    assert reachable_at < never_at
    assert ENGLISH.source_state_never_fetched not in page
    assert _NEVER_AN_AGE not in page


def test_the_empty_state_renders_when_no_source_exists() -> None:
    page = a_signed_in_lobby().get(SOURCES).text

    assert ENGLISH.sources_empty_title in page
    assert ENGLISH.sources_empty_explanation in page
    assert ENGLISH.sources_empty_need in page
    assert ENGLISH.sources_empty_ssh in page
    assert ENGLISH.sources_empty_or in page
    assert ENGLISH.sources_empty_https in page
    assert "<table" not in page
    assert ENGLISH.sources_fetch_now not in page


def test_fetch_now_of_a_name_this_instance_does_not_have_is_not_found() -> None:
    refused = fetch(a_signed_in_lobby(), "no-such-source")

    assert refused.status_code == HTTPStatus.NOT_FOUND


def test_fetch_now_from_another_site_is_refused(instance: Lobby) -> None:
    refused = fetch(
        instance.client,
        "decks",
        headers={"sec-fetch-site": "cross-site"},
    )

    assert refused.status_code == HTTPStatus.FORBIDDEN


def test_a_visitor_who_is_not_signed_in_is_sent_to_the_login() -> None:
    lobby = a_lobby(given=GivenDecks(source=a_configured_source(_ADDRESS)))

    reading = lobby.client.get(SOURCES)
    posting = fetch(lobby.client, "decks")

    assert reading.status_code == HTTPStatus.FOUND
    assert reading.headers["location"] == "/login/oidc?next=%2Fsettings%2Fsources"
    assert posting.status_code == HTTPStatus.FOUND
    assert (
        posting.headers["location"]
        == "/login/oidc?next=%2Fsettings%2Fsources%2Fdecks%2Ffetch"
    )


def test_the_list_and_the_empty_state_offer_add_source() -> None:
    empty = a_signed_in_lobby().get(SOURCES).text
    filled = (
        a_signed_in_lobby(GivenDecks(source=a_configured_source(_ADDRESS)))
        .get(SOURCES)
        .text
    )

    new = a_source_address(RESERVED_SOURCE_NAME)
    assert f'href="{new}">{ENGLISH.sources_add}<' in empty
    assert f'href="{new}">{ENGLISH.sources_add}<' in filled


def test_the_access_column_names_how_the_url_is_read() -> None:
    page = (
        a_signed_in_lobby(
            GivenDecks(source=a_configured_source(_ADDRESS)),
        )
        .get(SOURCES)
        .text
    )

    assert ENGLISH.sources_column_access in page
    assert ENGLISH.source_access_deploy_key in page


def test_a_created_source_is_listed_with_its_address_state_and_access() -> None:
    lobby = a_signed_in_lobby()
    a_created_source(lobby)

    listed = lobby.get(SOURCES).text

    assert "talks" in listed
    assert HTTPS in listed
    assert ENGLISH.source_state_reachable in listed
    assert ENGLISH.source_access_token in listed


def test_a_source_created_on_this_box_is_listed_with_its_address_and_tag() -> None:
    lobby = a_signed_in_lobby()
    a_created_source(lobby, _A_BOX_FOLDER)

    listed = lobby.get(SOURCES).text

    assert "talks" in listed
    assert str(_A_BOX_FOLDER["url"]) in listed
    assert ENGLISH.source_access_local in listed


def test_a_url_that_names_no_access_kind_has_no_access_tag() -> None:
    page = (
        a_signed_in_lobby(
            GivenDecks(
                source=a_configured_source("http://git.example.invalid/decks.git")
            ),
        )
        .get(SOURCES)
        .text
    )

    assert ENGLISH.source_access_token not in page
    assert ENGLISH.source_access_deploy_key not in page
    assert ENGLISH.source_access_local not in page


def test_an_existing_source_without_a_stored_secret_stays_when_another_is_added() -> (
    None
):
    existing = a_configured_source(_ADDRESS)
    lobby = a_signed_in_lobby(GivenDecks(source=existing))

    a_created_source(lobby)
    listed = lobby.get(SOURCES).text

    assert "decks" in listed
    assert _ADDRESS in listed
    assert ENGLISH.source_access_deploy_key in listed
    assert "talks" in listed


def a_source_address(name: str) -> str:
    return f"{SOURCES}/{name}"


@pytest.mark.parametrize("username", [USERNAME, NEIGHBOUR])
@pytest.mark.parametrize("name", [RESERVED_SOURCE_NAME, "talks"])
def test_the_add_and_a_source_address_answer_the_apps_shell_to_any_signed_in_person(
    username: str, name: str
) -> None:
    lobby = an_instance_of_two_people()
    a_created_source(lobby.client)
    signed_in_as(lobby, username)

    answer = lobby.client.get(a_source_address(name))

    assert answer.status_code == HTTPStatus.OK
    assert A_LOBBY_ASSET in answer.text
    assert answer.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("name", [RESERVED_SOURCE_NAME, "talks"])
def test_a_visitor_who_is_not_signed_in_is_sent_to_sign_in_from_a_source_address(
    name: str,
) -> None:
    answer = a_lobby().client.get(a_source_address(name))

    assert answer.status_code == HTTPStatus.FOUND
    assert answer.headers["location"] == (
        f"/login/oidc?next=%2Fsettings%2Fsources%2F{name}"
    )


def two_admin_sessions() -> tuple[TestClient, TestClient]:
    """Two browser sessions over one app, each with its own cookie jar."""
    first = a_lobby()
    first.sign_in()
    second = first.another_browser()
    second.sign_in(username=NEIGHBOUR, role=Role.ADMIN)
    return first.client, second.client


def test_the_shell_at_a_sources_address_carries_neither_a_minted_secret_nor_a_sent_token() -> (  # noqa: E501 - the name states the property
    None
):
    creator, other = two_admin_sessions()
    created = a_created_source(creator)
    renewed = creator.post("/api/sources/talks/webhook-secret").json()["webhook_secret"]

    for session in (creator, other):
        shell = session.get(a_source_address("talks"))
        assert shell.status_code == HTTPStatus.OK
        for secret in (created, renewed, SENT):
            assert secret not in shell.text


def test_the_list_links_each_name_to_its_page() -> None:
    page = (
        a_signed_in_lobby(GivenDecks(source=a_configured_source(_ADDRESS)))
        .get(SOURCES)
        .text
    )

    assert f'href="{SOURCES}/decks"' in page


def test_asking_to_remove_a_source_shows_the_confirm_and_deletes_nothing() -> None:
    source = a_configured_source(_ADDRESS)
    lobby = a_signed_in_lobby(GivenDecks(source=source, folders=(a_folder(),)))

    asked = ask_removal(lobby, source.name)

    assert asked.status_code == HTTPStatus.OK
    assert ENGLISH.source_remove_confirm_title.format(name=source.name) in asked.text
    assert ENGLISH.source_remove_cancel in asked.text
    assert ENGLISH.source_remove_confirm in asked.text
    assert 'class="btn"' in asked.text
    assert 'class="btn btn--primary"' in asked.text
    assert f'href="{SOURCES}/{source.name}"' in lobby.get(SOURCES).text


def test_the_confirm_sentence_is_singular_for_one_deck_and_one_run() -> None:
    source = a_configured_source(_ADDRESS)
    lobby = a_signed_in_lobby(GivenDecks(source=source, folders=(a_folder(),)))

    asked = ask_removal(lobby, source.name)

    assert (
        ENGLISH.source_remove_confirm_body.format(
            decks=1,
            deck_word=ENGLISH.source_remove_deck_singular,
            talk_word=ENGLISH.source_remove_talk_singular,
            runs=1,
            run_word=ENGLISH.source_remove_run_singular,
        )
        in asked.text
    )


def test_the_confirm_sentence_is_plural_for_more_than_one_deck_or_run() -> None:
    source = a_configured_source(_ADDRESS)
    lobby = a_signed_in_lobby(
        GivenDecks(
            source=source,
            folders=(a_folder("kundenfeedback"), a_folder("knowledge-fabric")),
        ),
    )
    fetch(lobby, source.name)

    asked = ask_removal(lobby, source.name)

    assert (
        ENGLISH.source_remove_confirm_body.format(
            decks=2,
            deck_word=ENGLISH.source_remove_deck_plural,
            talk_word=ENGLISH.source_remove_talk_plural,
            runs=2,
            run_word=ENGLISH.source_remove_run_plural,
        )
        in asked.text
    )


def test_confirming_removal_deletes_the_source_and_notices_the_list() -> None:
    source = a_configured_source(_ADDRESS)
    lobby = a_signed_in_lobby(GivenDecks(source=source, folders=(a_folder(),)))

    confirmed = confirm_removal(lobby, source.name)

    assert confirmed.status_code == HTTPStatus.SEE_OTHER
    assert confirmed.headers["location"] == SOURCES
    listed = lobby.get(SOURCES).text
    assert f'href="{SOURCES}/{source.name}"' not in listed
    assert (
        ENGLISH.source_removed.format(
            name=source.name,
            decks=1,
            deck_word=ENGLISH.source_remove_deck_singular,
            runs=1,
            run_word=ENGLISH.source_remove_run_singular,
        )
        in listed
    )
    assert (
        lobby.get(f"/api/sources/{source.name}", headers=_JSON).status_code
        == HTTPStatus.NOT_FOUND
    )


def test_a_stuck_mirror_refuses_the_removal_and_offers_the_counts_again() -> None:
    source = a_configured_source(_ADDRESS)
    lobby = a_signed_in_lobby(GivenDecks(source=source, stuck_mirror=True))

    confirmed = confirm_removal(lobby, source.name)

    assert confirmed.status_code == HTTPStatus.OK
    assert ENGLISH.source_remove_refused in confirmed.text
    assert ENGLISH.source_remove_confirm in confirmed.text
    assert DECK_COUNT_FIELD.search(confirmed.text) is not None
    assert RUN_COUNT_FIELD.search(confirmed.text) is not None
    assert f'href="{SOURCES}/{source.name}"' in lobby.get(SOURCES).text


def test_a_source_gone_between_the_refusal_and_the_page_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row this call itself never touched can still vanish under another one.

    A refusal never deletes a row, so the name it just refused to remove
    stays a source of this instance's own — except for whatever a second,
    concurrent request did to it in between, which this route answers the
    same way it answers any other name it does not have.
    """

    def gone(self: Decks, name: str) -> None:
        del self, name

    source = a_configured_source(_ADDRESS)
    lobby = a_signed_in_lobby(GivenDecks(source=source, stuck_mirror=True))
    asked = ask_removal(lobby, source.name)
    deck_count = DECK_COUNT_FIELD.search(asked.text)
    run_count = RUN_COUNT_FIELD.search(asked.text)
    assert deck_count is not None
    assert run_count is not None
    monkeypatch.setattr(Decks, "removal", gone)

    confirmed = lobby.post(
        REMOVE_CONFIRMED.format(name=source.name),
        data={"deck_count": deck_count.group(1), "run_count": run_count.group(1)},
    )

    assert confirmed.status_code == HTTPStatus.NOT_FOUND


def test_a_stale_confirm_shows_a_fresh_one_and_deletes_nothing() -> None:
    source = a_configured_source(_ADDRESS)
    lobby = a_signed_in_lobby(GivenDecks(source=source, folders=(a_folder(),)))
    stale = ask_removal(lobby, source.name)
    stale_decks = DECK_COUNT_FIELD.search(stale.text)
    stale_runs = RUN_COUNT_FIELD.search(stale.text)
    assert stale_decks is not None
    assert stale_runs is not None
    fetch(lobby, source.name)

    confirmed = lobby.post(
        REMOVE_CONFIRMED.format(name=source.name),
        data={"deck_count": stale_decks.group(1), "run_count": stale_runs.group(1)},
    )

    assert confirmed.status_code == HTTPStatus.OK
    assert (
        ENGLISH.source_remove_confirm_title.format(name=source.name) in confirmed.text
    )
    assert f'href="{SOURCES}/{source.name}"' in lobby.get(SOURCES).text


def test_get_on_either_remove_address_is_not_allowed_and_writes_nothing() -> None:
    source = a_configured_source(_ADDRESS)
    lobby = a_signed_in_lobby(GivenDecks(source=source))

    ask_get = lobby.get(REMOVE.format(name=source.name))
    confirm_get = lobby.get(REMOVE_CONFIRMED.format(name=source.name))

    assert ask_get.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert confirm_get.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert f'href="{SOURCES}/{source.name}"' in lobby.get(SOURCES).text


def test_a_webhook_for_a_removed_sources_name_is_not_found() -> None:
    source = a_configured_source(_ADDRESS)
    lobby = a_signed_in_lobby(GivenDecks(source=source))

    confirm_removal(lobby, source.name)
    called = lobby.post(
        hook_address(source.name),
        headers={"authorization": "Bearer whatever-the-old-secret-was"},
    )

    assert called.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize("step", ["ask", "confirm"])
def test_only_an_admin_may_remove_a_source(instance: Lobby, step: str) -> None:
    signed_in_as(instance, NEIGHBOUR)

    refused = (
        ask_removal(instance.client, "decks")
        if step == "ask"
        else confirm_removal(instance.client, "decks")
    )

    assert refused.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.parametrize("step", ["ask", "confirm"])
def test_removal_posts_from_another_site_are_refused(
    instance: Lobby,
    step: str,
) -> None:
    headers = {
        "origin": "https://another.example",
        "sec-fetch-site": "cross-site",
    }

    refused = (
        ask_removal(instance.client, "decks", headers=headers)
        if step == "ask"
        else confirm_removal(instance.client, "decks", headers=headers)
    )

    assert refused.status_code == HTTPStatus.FORBIDDEN
    assert f'href="{SOURCES}/decks"' in instance.client.get(SOURCES).text


@pytest.mark.parametrize("step", ["ask", "confirm"])
def test_removal_of_a_name_this_instance_does_not_have_is_not_found(step: str) -> None:
    lobby = a_signed_in_lobby()

    refused = (
        ask_removal(lobby, "no-such-source")
        if step == "ask"
        else confirm_removal(lobby, "no-such-source")
    )

    assert refused.status_code == HTTPStatus.NOT_FOUND


def test_after_removal_the_same_url_and_name_can_be_added_again() -> None:
    lobby = a_signed_in_lobby()
    a_created_source(lobby)

    confirm_removal(lobby, "talks")
    re_created = a_created_source(lobby)

    assert re_created
    assert lobby.get("/api/sources/talks", headers=_JSON).status_code == HTTPStatus.OK


def test_a_removed_decks_talk_answers_not_found(tmp_path: Path) -> None:
    source = a_configured_source(_ADDRESS)
    built = tmp_path / "builds" / "run" / "out"
    talk = built / "talk"
    talk.mkdir(parents=True)
    (talk / "index.html").write_text("<html></html>", encoding="utf-8")
    store = FakeDeckStore()
    store.put(
        Deck(
            slug="kundenfeedback",
            title="A talk",
            changed_at=NOW,
            owner_id=ADMIN,
            source_id=source.id,
            commit=_COMMIT,
            build=None,
            attempt=None,
        ),
    )
    store.put_build(
        "kundenfeedback",
        Build(directory=talk, pdf=built / "deck.pdf", commit=_COMMIT, built_at=NOW),
    )
    builder = FakeBuildRunner(root=tmp_path / "builds")
    lobby = a_signed_in_lobby(GivenDecks(source=source, store=store, builder=builder))

    before = lobby.get("/api/decks/kundenfeedback")
    confirm_removal(lobby, source.name)
    after = lobby.get("/api/decks/kundenfeedback")

    assert before.status_code == HTTPStatus.OK
    assert after.status_code == HTTPStatus.NOT_FOUND
