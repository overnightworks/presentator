"""A voice whose worker is missing or has no weights is not offered as ready."""

import os

import pytest

from presentator_speech import voices
from presentator_speech.config import Settings
from presentator_speech.voices import VoiceId, VoiceRuntimeState, VoiceState, statuses


@pytest.mark.parametrize(
    ("usable", "expected"),
    [
        pytest.param((), VoiceState.UNAVAILABLE, id="without-worker"),
        pytest.param(
            (VoiceId.CHATTERBOX,),
            VoiceState.NOT_DOWNLOADED,
            id="worker-without-weights",
        ),
    ],
)
def test_chatterbox_state_follows_worker_and_weights(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    usable: tuple[VoiceId, ...],
    expected: VoiceState,
) -> None:
    monkeypatch.setattr(voices, "installed_voice_ids", lambda _s: {VoiceId.PIPER})
    monkeypatch.setattr(
        voices,
        "provider_entrypoint_is_usable",
        lambda _root, provider: provider in usable,
    )
    settings = Settings(
        voice_cache=tmp_path,
        huggingface_cache=tmp_path,
        provider_root=tmp_path,
        PRESENTATOR_RUNTIME_UID=os.geteuid(),
    )
    runtime = VoiceRuntimeState(
        selected=None,
        ready=False,
        loading=False,
        pending=None,
        failed=None,
        qwen_downloading=False,
        qwen_download_failed=False,
    )

    row = next(r for r in statuses(settings, runtime) if r.id is VoiceId.CHATTERBOX)

    assert row.state is expected
