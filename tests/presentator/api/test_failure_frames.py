"""What a log line may say of an unexpected failure."""

from collections.abc import Callable

import pytest

from presentator.api.failure_frames import failure_frames

_SECRET = "https://objects.example/decks at /srv/decks/a.pdf"  # noqa: S105 - a message, no password


def _raise_endpoint_failure() -> None:
    raise ConnectionError(_SECRET)


def _wrap_endpoint_failure() -> None:
    try:
        _raise_endpoint_failure()
    except ConnectionError as cause:
        message = f"unavailable: {_SECRET}"
        raise RuntimeError(message) from cause


def _raise_group() -> None:
    message = "loop"
    raise ExceptionGroup(message, [ValueError(_SECRET), KeyError(_SECRET)])


def _caught(raiser: Callable[[], None]) -> BaseException:
    with pytest.raises(BaseException) as caught:  # noqa: PT011 - any error is the subject
        raiser()
    return caught.value


@pytest.mark.parametrize(
    ("raiser", "types"),
    [
        pytest.param(
            _wrap_endpoint_failure, ["RuntimeError", "ConnectionError"], id="cause"
        ),
        pytest.param(
            _raise_group, ["ExceptionGroup", "ValueError", "KeyError"], id="group"
        ),
    ],
)
def test_frames_name_every_error_and_the_function_that_raised_it(
    raiser: Callable[[], None], types: list[str]
) -> None:
    frames = failure_frames(_caught(raiser))

    assert [line for line in frames.splitlines() if not line.startswith(" ")] == types
    assert f"{__name__}.{raiser.__name__}:" in frames


@pytest.mark.parametrize("raiser", [_wrap_endpoint_failure, _raise_group])
def test_frames_hold_no_message_and_no_path(raiser: Callable[[], None]) -> None:
    frames = failure_frames(_caught(raiser))

    assert "objects.example" not in frames
    assert "/" not in frames
