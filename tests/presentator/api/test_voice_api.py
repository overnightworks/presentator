"""`/api/voice` and its load, download and sample: an admin's, as data."""

from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path

import pytest
from httpx2 import Response

from presentator.api.voice_api import VoiceView
from presentator.application.voice import VoiceStatusUse
from presentator.contracts.voice import (
    SampleLanguage,
    VoiceDownloadOutcome,
    VoiceId,
    VoiceLoadOutcome,
    VoiceRecovery,
    VoiceRecoveryKind,
    VoiceSampleBusyError,
    VoiceSnapshot,
    VoiceState,
    VoiceStatus,
    VoiceUnavailableError,
)
from tests.support.lobby import NEIGHBOUR, Lobby, a_lobby
from tests.support.sign_in_support import ADMIN, MEMBER, Scene, a_scene

_JSON = {"accept": "application/json"}
_HAL = "hal-client"
_PROBLEM = "application/problem+json"
_CROSS_SITE = {"sec-fetch-site": "cross-site"}
_WAV = b"RIFF....WAVEfmt "
_PIPER_LOAD = f"/api/voice/{VoiceId.PIPER}/load"
_PIPER_SAMPLE = f"/api/voice/{VoiceId.PIPER}/sample"
_QWEN_DOWNLOAD = f"/api/voice/{VoiceId.QWEN}/download"


def a_snapshot(
    *,
    states: dict[VoiceId, VoiceState] | None = None,
    recovery: VoiceRecovery | None = None,
    order: tuple[VoiceId, ...] = tuple(VoiceId),
) -> VoiceSnapshot:
    given = states or {}
    return VoiceSnapshot(
        voices=tuple(
            VoiceStatus(
                id=voice,
                name=f"name of {voice.value}",
                language="de, en",
                state=given.get(voice, VoiceState.NOT_DOWNLOADED),
            )
            for voice in order
        ),
        recovery=recovery,
    )


@dataclass
class FakeSpeech:
    """A private speech port that counts its calls and answers what a test sets."""

    snapshots: list[VoiceSnapshot | VoiceUnavailableError] = field(
        default_factory=lambda: [a_snapshot()]
    )
    load_answer: VoiceLoadOutcome | VoiceUnavailableError = VoiceLoadOutcome.ACTIVATED
    download_answer: VoiceDownloadOutcome | VoiceUnavailableError = (
        VoiceDownloadOutcome.STARTED
    )
    sample_answer: bytes | VoiceUnavailableError = _WAV
    calls: list[str] = field(default_factory=list[str])
    loaded: list[VoiceId] = field(default_factory=list[VoiceId])
    sampled: list[tuple[VoiceId, SampleLanguage]] = field(
        default_factory=list[tuple[VoiceId, SampleLanguage]]
    )

    async def snapshot(self) -> VoiceSnapshot:
        self.calls.append("snapshot")
        answer = (
            self.snapshots[0] if len(self.snapshots) == 1 else self.snapshots.pop(0)
        )
        if isinstance(answer, VoiceUnavailableError):
            raise answer
        return answer

    async def load(self, voice: VoiceId) -> VoiceLoadOutcome:
        self.calls.append("load")
        self.loaded.append(voice)
        if isinstance(self.load_answer, VoiceUnavailableError):
            raise self.load_answer
        return self.load_answer

    async def download_qwen(self) -> VoiceDownloadOutcome:
        self.calls.append("download")
        if isinstance(self.download_answer, VoiceUnavailableError):
            raise self.download_answer
        return self.download_answer

    async def sample(self, voice: VoiceId, language: SampleLanguage) -> bytes:
        self.calls.append("sample")
        self.sampled.append((voice, language))
        if isinstance(self.sample_answer, VoiceUnavailableError):
            raise self.sample_answer
        return self.sample_answer


type Call = Callable[[Lobby], Response]


def _get(lobby: Lobby) -> Response:
    return lobby.client.get("/api/voice", headers=_JSON)


def _load(lobby: Lobby) -> Response:
    return lobby.client.post(_PIPER_LOAD, headers=_JSON)


def _download(lobby: Lobby) -> Response:
    return lobby.client.post(_QWEN_DOWNLOAD, headers=_JSON)


def _sample(lobby: Lobby) -> Response:
    return lobby.client.post(f"{_PIPER_SAMPLE}/{SampleLanguage.GERMAN}", headers=_JSON)


EVERY_ROUTE = pytest.mark.parametrize(
    "call",
    [_get, _load, _download, _sample],
    ids=["status", "load", "download", "sample"],
)


def an_admins_lobby(speech: FakeSpeech) -> Lobby:
    lobby = a_lobby(voice=VoiceStatusUse(private=speech))
    lobby.sign_in()
    return lobby


@EVERY_ROUTE
def test_signed_out_is_webauths_refusal_not_a_problem(call: Call) -> None:
    speech = FakeSpeech()
    lobby = a_lobby(voice=VoiceStatusUse(private=speech))

    answer = call(lobby)

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert answer.headers["content-type"] != _PROBLEM
    assert speech.calls == []


@EVERY_ROUTE
def test_a_member_is_refused_with_a_problem_before_any_private_call(
    call: Call,
) -> None:
    speech = FakeSpeech()
    lobby = a_lobby(voice=VoiceStatusUse(private=speech))
    lobby.sign_in(username=NEIGHBOUR)

    answer = call(lobby)

    assert answer.status_code == HTTPStatus.FORBIDDEN
    assert answer.headers["content-type"] == _PROBLEM
    assert answer.json()["status"] == HTTPStatus.FORBIDDEN
    assert speech.calls == []


def test_the_admin_reads_the_five_rows_in_order_without_recovery() -> None:
    speech = FakeSpeech(
        snapshots=[a_snapshot(states={VoiceId.CHATTERBOX: VoiceState.ACTIVE})]
    )

    answer = _get(an_admins_lobby(speech))

    assert answer.status_code == HTTPStatus.OK
    assert answer.headers["cache-control"] == "no-store"
    view = VoiceView.model_validate(answer.json())
    assert [row.id for row in view.voices] == list(VoiceId)
    assert [row.state for row in view.voices if row.state is VoiceState.ACTIVE] == [
        VoiceState.ACTIVE
    ]
    assert view.voices[1].name == "name of chatterbox"
    assert view.voices[1].language == "de, en"
    assert view.recovery is None


def test_the_admin_reads_the_recovery_that_names_a_voice() -> None:
    speech = FakeSpeech(
        snapshots=[
            a_snapshot(
                recovery=VoiceRecovery(
                    kind=VoiceRecoveryKind.LOAD_FAILED, voice=VoiceId.VOXCPM
                )
            )
        ]
    )

    answer = _get(an_admins_lobby(speech))

    assert answer.json()["recovery"] == {
        "kind": VoiceRecoveryKind.LOAD_FAILED.value,
        "voice": VoiceId.VOXCPM.value,
    }


@pytest.mark.parametrize(
    "unverifiable",
    [
        VoiceUnavailableError(),
        a_snapshot(order=tuple(reversed(VoiceId))),
    ],
    ids=["unavailable", "catalogue-in-another-order"],
)
def test_a_snapshot_that_cannot_be_verified_is_a_503_problem(
    unverifiable: VoiceSnapshot | VoiceUnavailableError,
) -> None:
    answer = _get(an_admins_lobby(FakeSpeech(snapshots=[unverifiable])))

    assert answer.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert answer.headers["content-type"] == _PROBLEM


def test_every_poll_answers_the_private_state_of_that_moment() -> None:
    speech = FakeSpeech(
        snapshots=[
            a_snapshot(states={VoiceId.QWEN: VoiceState.DOWNLOADING}),
            a_snapshot(states={VoiceId.QWEN: VoiceState.DOWNLOADED}),
        ]
    )
    lobby = an_admins_lobby(speech)

    states = [
        VoiceView.model_validate(_get(lobby).json()).voices[2].state for _ in range(2)
    ]

    assert states == [VoiceState.DOWNLOADING, VoiceState.DOWNLOADED]


@pytest.mark.parametrize("outcome", list(VoiceLoadOutcome))
def test_a_load_answers_the_outcome(outcome: VoiceLoadOutcome) -> None:
    speech = FakeSpeech(load_answer=outcome)

    answer = _load(an_admins_lobby(speech))

    assert answer.status_code == HTTPStatus.OK
    assert answer.json() == {"outcome": outcome.value}
    assert answer.headers["cache-control"] == "no-store"
    assert speech.loaded == [VoiceId.PIPER]


@pytest.mark.parametrize("outcome", list(VoiceDownloadOutcome))
def test_a_download_answers_the_outcome(outcome: VoiceDownloadOutcome) -> None:
    speech = FakeSpeech(download_answer=outcome)

    answer = _download(an_admins_lobby(speech))

    assert answer.status_code == HTTPStatus.OK
    assert answer.json() == {"outcome": outcome.value}
    assert answer.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("call", [_load, _download], ids=["load", "download"])
def test_an_unavailable_action_is_a_503_problem(call: Call) -> None:
    speech = FakeSpeech(
        load_answer=VoiceUnavailableError(), download_answer=VoiceUnavailableError()
    )

    answer = call(an_admins_lobby(speech))

    assert answer.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert answer.headers["content-type"] == _PROBLEM


def test_a_sample_is_the_wav_bytes_the_private_service_made() -> None:
    speech = FakeSpeech()

    answer = _sample(an_admins_lobby(speech))

    assert answer.status_code == HTTPStatus.OK
    assert answer.headers["content-type"] == "audio/wav"
    assert answer.headers["cache-control"] == "no-store"
    assert answer.content == _WAV
    assert speech.sampled == [(VoiceId.PIPER, SampleLanguage.GERMAN)]


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (VoiceSampleBusyError(), HTTPStatus.CONFLICT),
        (VoiceUnavailableError(), HTTPStatus.SERVICE_UNAVAILABLE),
    ],
    ids=["busy", "unavailable"],
)
def test_a_sample_that_cannot_be_made_is_a_problem(
    failure: VoiceUnavailableError, status: HTTPStatus
) -> None:
    answer = _sample(an_admins_lobby(FakeSpeech(sample_answer=failure)))

    assert answer.status_code == status
    assert answer.headers["content-type"] == _PROBLEM


@pytest.mark.parametrize(
    "address",
    [
        "/api/voice/nonsense/load",
        "/api/voice/nonsense/sample/de",
        "/api/voice/piper/sample/fr",
    ],
)
def test_a_value_outside_the_closed_sets_keeps_the_frameworks_validation_answer(
    address: str,
) -> None:
    speech = FakeSpeech()

    answer = an_admins_lobby(speech).client.post(address, headers=_JSON)

    assert answer.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert speech.calls == []


@pytest.mark.parametrize(
    "address",
    [_PIPER_LOAD, _QWEN_DOWNLOAD, f"{_PIPER_SAMPLE}/{SampleLanguage.ENGLISH}"],
)
def test_a_cross_site_post_is_refused_by_the_origin_check_before_any_call(
    address: str,
) -> None:
    speech = FakeSpeech()

    answer = an_admins_lobby(speech).client.post(
        address, headers={**_JSON, **_CROSS_SITE}
    )

    assert answer.status_code == HTTPStatus.FORBIDDEN
    assert speech.calls == []


@pytest.mark.parametrize(
    ("method", "address"),
    [
        ("GET", _PIPER_LOAD),
        ("GET", _QWEN_DOWNLOAD),
        ("GET", f"{_PIPER_SAMPLE}/{SampleLanguage.GERMAN}"),
        ("POST", "/api/voice"),
        ("PUT", "/api/voice"),
    ],
)
def test_the_wrong_method_is_the_frameworks_405(method: str, address: str) -> None:
    speech = FakeSpeech()

    answer = an_admins_lobby(speech).client.request(method, address, headers=_JSON)

    assert answer.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert speech.calls == []


@pytest.fixture
def scene(tmp_path: Path) -> Scene:
    return a_scene(tmp_path, token_clients=frozenset({_HAL}))


def a_bearer(scene: Scene, groups: list[str]) -> dict[str, str]:
    minted = scene.issuer.mint_access_token(
        subject="a-person", groups=groups, authorized_party=_HAL, audience=None
    )
    return {"authorization": f"Bearer {minted}", **_JSON}


_BEARER_ROUTES = pytest.mark.parametrize(
    ("method", "address"),
    [
        ("GET", "/api/voice"),
        ("POST", _PIPER_LOAD),
        ("POST", _QWEN_DOWNLOAD),
        ("POST", f"{_PIPER_SAMPLE}/{SampleLanguage.GERMAN}"),
    ],
    ids=["status", "load", "download", "sample"],
)


@_BEARER_ROUTES
def test_an_admins_bearer_alone_reaches_the_route_as_the_cookie_does(
    scene: Scene, method: str, address: str
) -> None:
    answer = scene.browser().request(method, address, headers=a_bearer(scene, ADMIN))

    # The scene's private speech is the default arrangement: it is unavailable,
    # so only a request that passed the admin check can answer this problem.
    assert answer.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert answer.headers["content-type"] == _PROBLEM


@_BEARER_ROUTES
def test_a_members_bearer_is_refused_with_a_problem(
    scene: Scene, method: str, address: str
) -> None:
    answer = scene.browser().request(method, address, headers=a_bearer(scene, MEMBER))

    assert answer.status_code == HTTPStatus.FORBIDDEN
    assert answer.headers["content-type"] == _PROBLEM
