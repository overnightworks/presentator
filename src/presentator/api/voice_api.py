"""`/api/voice`: the voice catalogue and its three actions, as data, for an admin.

Every route is admin only; the refusal comes before the private service is
called. The wire carries enums and the fixed sample, never worded sentences.
"""

from collections.abc import Generator
from contextlib import contextmanager
from http import HTTPStatus
from typing import Final

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from presentator.api.admission import gated_admin
from presentator.api.problems import ProblemError, problem_response
from presentator.application.voice import VoiceStatusUse
from presentator.contracts.voice import (
    SampleLanguage,
    VoiceDownloadOutcome,
    VoiceId,
    VoiceLoadOutcome,
    VoiceRecoveryKind,
    VoiceSampleBusyError,
    VoiceState,
    VoiceUnavailableError,
)

_VOICE: Final = "/api/voice"
_WAV: Final = "audio/wav"
_UNAVAILABLE: Final = problem_response(HTTPStatus.SERVICE_UNAVAILABLE)


class VoiceRowView(BaseModel):
    """One voice of the closed catalogue and where it stands."""

    model_config = ConfigDict(frozen=True)

    id: VoiceId
    name: str
    language: str
    state: VoiceState


class VoiceRecoveryView(BaseModel):
    """Why a selection needs the admin's attention, and which voice it names."""

    model_config = ConfigDict(frozen=True)

    kind: VoiceRecoveryKind
    voice: VoiceId | None


class VoiceView(BaseModel):
    """The verified catalogue in the owner's order and its recovery, if any."""

    model_config = ConfigDict(frozen=True)

    voices: list[VoiceRowView]
    recovery: VoiceRecoveryView | None


class LoadView(BaseModel):
    """What the private service did with a load."""

    model_config = ConfigDict(frozen=True)

    outcome: VoiceLoadOutcome


class DownloadView(BaseModel):
    """What the private service did with the Qwen download."""

    model_config = ConfigDict(frozen=True)

    outcome: VoiceDownloadOutcome


@contextmanager
def _private_service() -> Generator[None]:
    """A busy private service is a 409 problem; one that cannot answer, a 503."""
    try:
        yield
    except VoiceSampleBusyError:
        raise ProblemError(HTTPStatus.CONFLICT) from None
    except VoiceUnavailableError:
        raise ProblemError(HTTPStatus.SERVICE_UNAVAILABLE) from None


def voice_api_routes(*, voice: VoiceStatusUse) -> APIRouter:
    """The router that answers the admin's voice catalogue and actions."""
    router = APIRouter(
        dependencies=[Depends(gated_admin)],
        responses=problem_response(HTTPStatus.FORBIDDEN) | _UNAVAILABLE,
    )

    @router.get(_VOICE)
    async def status() -> VoiceView:
        with _private_service():
            snapshot = await voice.snapshot()
        recovery = snapshot.recovery
        return VoiceView(
            voices=[
                VoiceRowView(
                    id=row.id, name=row.name, language=row.language, state=row.state
                )
                for row in snapshot.voices
            ],
            recovery=None
            if recovery is None
            else VoiceRecoveryView(kind=recovery.kind, voice=recovery.voice),
        )

    @router.post(f"{_VOICE}/{{voice_id}}/load")
    async def load(voice_id: VoiceId) -> LoadView:
        with _private_service():
            return LoadView(outcome=await voice.load(voice_id))

    @router.post(f"{_VOICE}/{VoiceId.QWEN}/download")
    async def download() -> DownloadView:
        with _private_service():
            return DownloadView(outcome=await voice.download_qwen())

    @router.post(
        f"{_VOICE}/{{voice_id}}/sample/{{language}}",
        response_class=Response,
        responses={
            HTTPStatus.OK.value: {"content": {_WAV: {}}},
            **problem_response(HTTPStatus.CONFLICT),
        },
    )
    async def sample(voice_id: VoiceId, language: SampleLanguage) -> Response:
        with _private_service():
            audio = await voice.sample(voice_id, language)
        return Response(content=audio, media_type=_WAV)

    return router
