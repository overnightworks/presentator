"""The lobby's built shell: read once, hashed for the policy, served per reader."""

from http import HTTPStatus
from pathlib import Path

import pytest
from webauth.policies import default_content_security_policy

from presentator.api.lobby_shell import (
    LANGUAGE_MARK,
    THEME_MARK,
    LobbyBuildError,
    LobbyShell,
)
from presentator.contracts.preferences import (
    Appearance,
    PersonPreferences,
    ThemeChoice,
)
from tests.presentator.application.support.fakes import FakePersonPreferencesStore
from tests.support.lobby import (
    A_LOBBY_ASSET,
    ADMIN,
    ENGLISH,
    LOBBY_SHELL,
    Lobby,
    a_lobby,
)

_HTML = (
    f'<html lang="{LANGUAGE_MARK}" data-theme="{THEME_MARK}"><body>{{}}</body></html>'
)
_BOOTSTRAP = "<script>import('/_app/start.js');</script>"
_REBUILT_BOOTSTRAP = "<script>import('/_app/rebuilt.js');</script>"


def _a_build(directory: Path, page: str) -> Path:
    (directory / "_app").mkdir(parents=True)
    (directory / "index.html").write_text(page, encoding="utf-8")
    return directory


def test_the_shell_is_filled_with_the_readers_language_and_theme(
    tmp_path: Path,
) -> None:
    shell = LobbyShell.read(_a_build(tmp_path, _HTML.format(_BOOTSTRAP)))

    page = shell.filled(Appearance(text=ENGLISH, theme=ThemeChoice.DARK))

    assert f'lang="{ENGLISH.language_tag}"' in page
    assert 'data-theme="dark"' in page


def test_the_bootstrap_hash_is_a_source_the_policy_takes(tmp_path: Path) -> None:
    shell = LobbyShell.read(_a_build(tmp_path, _HTML.format(_BOOTSTRAP)))

    policy = default_content_security_policy([shell.bootstrap_hash])

    assert f"'{shell.bootstrap_hash}'" in policy


def test_the_bootstrap_hash_follows_the_built_script(tmp_path: Path) -> None:
    one = LobbyShell.read(_a_build(tmp_path / "one", _HTML.format(_BOOTSTRAP)))
    again = LobbyShell.read(_a_build(tmp_path / "again", _HTML.format(_BOOTSTRAP)))
    rebuilt = LobbyShell.read(
        _a_build(tmp_path / "rebuilt", _HTML.format(_REBUILT_BOOTSTRAP))
    )

    assert one.bootstrap_hash == again.bootstrap_hash
    assert one.bootstrap_hash != rebuilt.bootstrap_hash


@pytest.mark.parametrize(
    "page",
    [
        pytest.param(_HTML.format(""), id="no-inline-script"),
        pytest.param(_HTML.format(_BOOTSTRAP * 2), id="two-inline-scripts"),
        pytest.param(f"<html><body>{_BOOTSTRAP}</body></html>", id="no-marks"),
    ],
)
def test_a_shell_this_host_cannot_serve_is_refused(tmp_path: Path, page: str) -> None:
    with pytest.raises(LobbyBuildError):
        LobbyShell.read(_a_build(tmp_path, page))


def test_a_missing_build_is_refused(tmp_path: Path) -> None:
    with pytest.raises(LobbyBuildError, match="pnpm build"):
        LobbyShell.read(tmp_path)


def _directives(policy: str) -> dict[str, list[str]]:
    named = (part.split() for part in policy.split(";") if part.strip())
    return {name: sources for name, *sources in named}


def _is_the_lobby_app(answer_text: str) -> bool:
    return A_LOBBY_ASSET in answer_text


def test_the_signed_out_login_loads_the_lobby_app(lobby: Lobby) -> None:
    answer = lobby.client.get("/login")

    assert answer.status_code == HTTPStatus.OK
    assert _is_the_lobby_app(answer.text)
    assert f'lang="{ENGLISH.language_tag}"' in answer.text


def test_the_lobby_app_opens_in_the_readers_own_theme() -> None:
    lobby = a_lobby(
        person_preferences=FakePersonPreferencesStore(
            chosen={ADMIN: PersonPreferences(theme=ThemeChoice.DARK)}
        )
    )
    lobby.sign_in()

    assert 'data-theme="dark"' in lobby.client.get("/an-address-the-app-decides").text


def test_an_address_no_route_claims_loads_the_lobby_app_for_a_signed_in_reader(
    signed_in_lobby: Lobby,
) -> None:
    answer = signed_in_lobby.client.get("/an-address-the-app-decides")

    assert answer.status_code == HTTPStatus.OK
    assert _is_the_lobby_app(answer.text)


@pytest.mark.parametrize(
    "path",
    [
        "/api/no-such-route",
        "/_app/no-such-asset.js",
        "/auth/no-such-step",
        "/static/no-such-sheet.css",
        "/setup",
    ],
)
def test_an_excluded_address_stays_the_frameworks_not_found(
    signed_in_lobby: Lobby, path: str
) -> None:
    answer = signed_in_lobby.client.get(path)

    assert answer.status_code == HTTPStatus.NOT_FOUND
    assert not _is_the_lobby_app(answer.text)


def test_a_json_caller_at_an_unclaimed_address_gets_the_frameworks_not_found(
    signed_in_lobby: Lobby,
) -> None:
    answer = signed_in_lobby.client.get(
        "/an-address-the-app-decides", headers={"accept": "application/json"}
    )

    assert answer.status_code == HTTPStatus.NOT_FOUND
    assert not _is_the_lobby_app(answer.text)


def test_the_lobby_apps_assets_load_without_signing_in(lobby: Lobby) -> None:
    answer = lobby.client.get(A_LOBBY_ASSET)

    assert answer.status_code == HTTPStatus.OK


def test_the_policy_admits_the_built_bootstrap_and_no_other_inline_script(
    lobby: Lobby,
) -> None:
    policy = lobby.client.get("/login").headers["content-security-policy"]

    assert _directives(policy)["script-src"] == [
        "'self'",
        f"'{LOBBY_SHELL.bootstrap_hash}'",
    ]


def test_the_policy_admits_one_style_attribute_by_its_hash_and_no_inline_style(
    lobby: Lobby,
) -> None:
    policy = lobby.client.get("/login").headers["content-security-policy"]

    self_keyword, unsafe_hashes, *hashes = _directives(policy)["style-src"]
    assert (self_keyword, unsafe_hashes) == ("'self'", "'unsafe-hashes'")
    assert [source.startswith("'sha256-") for source in hashes] == [True]
