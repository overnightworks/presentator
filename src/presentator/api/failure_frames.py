"""What a log line may say of an unexpected failure: its types and source frames."""

import traceback
from typing import cast


def failure_frames(error: BaseException) -> str:
    """The type and source frames of an error and of every error it holds or came from.

    A message can carry an address, a path or a transcript, so none is written; a
    frame names its module, function and line, never its file's path.
    """
    lines: list[str] = []
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        lines.append(type(current).__qualname__)
        lines.extend(
            f"  {frame.f_globals.get('__name__')}.{frame.f_code.co_qualname}:{line}"
            for frame, line in traceback.walk_tb(current.__traceback__)
        )
        pending.extend(reversed(_errors_behind(current)))
    return "\n".join(lines)


def _errors_behind(error: BaseException) -> list[BaseException]:
    members = (
        cast("BaseExceptionGroup[BaseException]", error).exceptions
        if isinstance(error, BaseExceptionGroup)
        else ()
    )
    return [
        *members,
        *(
            related
            for related in (error.__cause__, error.__context__)
            if related is not None
        ),
    ]
