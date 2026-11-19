"""A JSON body is UTF-8 and holds only strings every route can store and answer.

The framework's JSON reader decodes an escaped lone surrogate or an escaped NUL
into a `str`, and Pydantic refuses a lone surrogate only in a constrained
string. No answer encodes a lone surrogate as UTF-8, and Postgres keeps no NUL
in text. A body that is not UTF-8 makes the reader fail outside the validation
answer. This middleware reads every JSON body before a route does and answers
both as 422 in the validation answer's shape, naming where the fault is and
never what it is; a body that is not UTF-8 is `json_invalid`, as one that is not
JSON is. JSON syntax stays the framework's to judge. Since the body is read
here, a streamed body over its cap is answered by the size limit's 413.
"""

import json
import re
from dataclasses import dataclass
from email.message import Message as ContentType
from typing import Final, cast

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from presentator.api.problems import ValidationFault, validation_answer

UNSTORABLE_STRING: Final = "string_unstorable"
_JSON_INVALID: Final = "json_invalid"
_BODY: Final = "body"
_UNSTORABLE: Final = re.compile(r"[\x00\ud800-\udfff]")

type Location = tuple[str | int, ...]


def _declares_json(scope: Scope) -> bool:
    """The framework reads a body as JSON only under a JSON media type."""
    declared = Headers(scope=scope).get("content-type")
    if declared is None:
        return False
    content_type = ContentType()
    content_type["content-type"] = declared
    subtype = content_type.get_content_subtype()
    return content_type.get_content_maintype() == "application" and (
        subtype == "json" or subtype.endswith("+json")
    )


def _unstorable_at(document: object) -> Location | None:
    """Where a string, or a key of an object, holds a lone surrogate or a NUL.

    The walk keeps its own stack: a document nested as deep as the reader takes
    must not exhaust Python's.
    """
    pending: list[tuple[Location, object]] = [((_BODY,), document)]
    while pending:
        at, value = pending.pop()
        if isinstance(value, str):
            if _UNSTORABLE.search(value):
                return at
        elif isinstance(value, dict):
            members = cast("dict[str, object]", value)
            if any(_UNSTORABLE.search(key) for key in members):
                return at
            pending.extend(((*at, key), member) for key, member in members.items())
        elif isinstance(value, list):
            items = cast("list[object]", value)
            pending.extend(((*at, index), item) for index, item in enumerate(items))
    return None


def _body_fault(body: bytes) -> ValidationFault | None:
    """The fault no route may see, or None for a body the framework judges."""
    try:
        text = body.decode()
    except UnicodeDecodeError as undecodable:
        return ValidationFault(
            loc=(_BODY, undecodable.start),
            msg="Body should be UTF-8",
            type=_JSON_INVALID,
        )
    try:
        document: object = json.loads(text)
    except ValueError, RecursionError:
        return None
    at = _unstorable_at(document)
    if at is None:
        return None
    return ValidationFault(
        loc=at,
        msg="String should hold no lone surrogate and no NUL",
        type=UNSTORABLE_STRING,
    )


async def _whole_body(receive: Receive) -> tuple[bytes, Message | None]:
    """The body as sent, and the message that cut it short, if one did."""
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return b"".join(chunks), message
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            return b"".join(chunks), None


@dataclass(frozen=True, slots=True)
class StorableJsonBodies:
    """Pure ASGI middleware inside the gates, so it reads only admitted bodies.

    A path in `own_readers` serves a protocol that answers its own malformed
    messages, as the MCP front answers JSON-RPC; whatever it hands a route
    passes through here again.
    """

    app: ASGIApp
    own_readers: frozenset[str] = frozenset()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer a JSON body no route can take, and hand every other one on."""
        if (
            scope["type"] != "http"
            or scope["path"] in self.own_readers
            or not _declares_json(scope)
        ):
            await self.app(scope, receive, send)
            return
        body, cut_short = await _whole_body(receive)
        fault = None if cut_short is not None else _body_fault(body)
        if fault is not None:
            await validation_answer((fault,))(scope, receive, send)
            return
        handed: list[Message] = [
            {"type": "http.request", "body": body, "more_body": cut_short is not None}
        ]
        if cut_short is not None:
            handed.append(cut_short)

        async def replay() -> Message:
            return handed.pop(0) if handed else await receive()

        await self.app(scope, replay, send)
