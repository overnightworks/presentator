"""The admin routes that erase or export one person, behind hal's access token alone.

The path prefix takes a Keycloak access token and no cookie (ADR 0003); the
bodies are the shared `people_erasure` and `people_export` shapes of edge's
people contract.
"""

import io
import json
import logging
import math
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Final, assert_never

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from starlette.responses import Response
from webauth.middleware.bearer_gate import bearer_caller
from webauth.ports import BearerCaller

from presentator.application.people import People
from presentator.contracts.people import (
    KindCount,
    LeftoverDirectory,
    PersonBusy,
    PersonData,
    PersonDiskLeftover,
    PersonErased,
    PersonErasure,
    PersonExport,
)

ADMIN_PREFIX: Final = "/api/admin/"
_PEOPLE_ERASURE_VERSION: Final = 1
_PEOPLE_EXPORT_VERSION: Final = 1
_EXPORT_MEMBER: Final = "export.json"
# A fixed member time, so an export does not depend on the machine's file times.
_MEMBER_TIME: Final = (1980, 1, 1, 0, 0, 0)
_NOTHING_KEPT: Final = PersonData(
    account=None, sessions=(), preferences=None, key_draft=None, sources=()
)

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class AdminPeople:
    """The people use case and the clients whose tokens may call it, together.

    A route without the clients that admit its callers cannot be composed.
    """

    people: People
    admitted_clients: frozenset[str]
    clock: Callable[[], datetime]


def _counts(kinds: tuple[KindCount, ...]) -> list[dict[str, object]]:
    return [{"kind": counted.kind.value, "count": counted.count} for counted in kinds]


def _leftover(leftovers: tuple[LeftoverDirectory, ...]) -> list[dict[str, object]]:
    """One entry per directory that stayed, so each counts one."""
    return [
        {"kind": left.kind.value, "count": 1, "path": left.path.as_posix()}
        for left in leftovers
    ]


def _kinds_left(leftovers: tuple[LeftoverDirectory, ...]) -> tuple[KindCount, ...]:
    tally = Counter(left.kind for left in leftovers)
    return tuple(KindCount(kind=kind, count=count) for kind, count in tally.items())


def _erased_body(kinds: tuple[KindCount, ...], *, dry_run: bool) -> dict[str, object]:
    return {"dry_run": dry_run, "kinds": _counts(kinds), "affects_others": []}


def _envelope(app: str, subject: str) -> dict[str, object]:
    return {
        "people_erasure": _PEOPLE_ERASURE_VERSION,
        "app": app,
        "subject": subject,
    }


def _answer(
    outcome: PersonErasure, *, app: str, subject: str, dry_run: bool, wait_seconds: int
) -> JSONResponse:
    body = _envelope(app, subject)
    match outcome:
        case PersonErased(kinds=kinds):
            return JSONResponse(body | _erased_body(kinds, dry_run=dry_run))
        case PersonBusy(running=running):
            return JSONResponse(
                body | {"busy": _counts(running)},
                HTTPStatus.CONFLICT,
                headers={"Retry-After": str(wait_seconds)},
            )
        case PersonDiskLeftover(removed=removed, leftovers=leftovers):
            return JSONResponse(
                body
                | _erased_body(removed, dry_run=dry_run)
                | {"leftover": _leftover(leftovers)},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        case _:
            assert_never(outcome)


def _tallied(kinds: tuple[KindCount, ...]) -> str:
    return ", ".join(f"{one.kind.value}={one.count}" for one in kinds) or "nothing"


def _audited(outcome: PersonErasure, *, dry_run: bool) -> None:
    """One line per call: what was asked, how it ended, how many; nobody named."""
    operation = "dry run" if dry_run else "erase"
    match outcome:
        case PersonErased(kinds=kinds):
            verb = "would erase" if dry_run else "erased"
            _log.info("person %s: %s %s", operation, verb, _tallied(kinds))
        case PersonBusy(running=running):
            _log.info("person %s: busy with %s", operation, _tallied(running))
        case PersonDiskLeftover(removed=removed, leftovers=leftovers):
            _log.info(
                "person %s: left over %s, erased %s",
                operation,
                _tallied(_kinds_left(leftovers)),
                _tallied(removed),
            )
        case _:
            assert_never(outcome)


def _rfc3339(instant: datetime) -> str:
    return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _export_archive(
    export: PersonExport, *, app: str, subject: str, now: datetime
) -> bytes:
    """The ZIP whose root holds `export.json` alone; files stay empty."""
    nothing_kept = export.data == _NOTHING_KEPT
    document = {
        "people_export": _PEOPLE_EXPORT_VERSION,
        "app": app,
        "subject": subject,
        "exported_at": _rfc3339(now.replace(microsecond=0)),
        "data": {} if nothing_kept else asdict(export.data),
        "files": [],
        "withheld": [
            {
                "kind": one.kind.value,
                "reason": one.reason.value,
                "count": one.count,
            }
            for one in export.withheld
        ],
    }
    member = zipfile.ZipInfo(_EXPORT_MEMBER, date_time=_MEMBER_TIME)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(
            member,
            json.dumps(document, default=_rfc3339, sort_keys=True),
            compress_type=zipfile.ZIP_DEFLATED,
        )
    return archive.getvalue()


def _export_audited(export: PersonExport) -> None:
    held_back = ", ".join(f"{one.kind.value}={one.count}" for one in export.withheld)
    _log.info("person export: withheld %s", held_back or "nothing")


def people_routes(*, admin: AdminPeople, app: str) -> APIRouter:
    """The erase and export routes of the people contract, for the lobby to include."""
    people = admin.people
    wait_seconds = math.ceil(people.lock_wait.total_seconds())
    router = APIRouter()

    # A plain `def`: the wait for a running build blocks a pool thread, never the loop.
    @router.delete(f"{ADMIN_PREFIX}people/{{subject}}")
    def erase_person(
        subject: str,
        caller: Annotated[BearerCaller, Depends(bearer_caller)],
        *,
        dry_run: bool = False,
    ) -> Response:
        del caller
        outcome = people.erase_person(subject, dry_run=dry_run)
        _audited(outcome, dry_run=dry_run)
        return _answer(
            outcome,
            app=app,
            subject=subject,
            dry_run=dry_run,
            wait_seconds=wait_seconds,
        )

    @router.get(f"{ADMIN_PREFIX}people/{{subject}}/export")
    def export_person(
        subject: str,
        caller: Annotated[BearerCaller, Depends(bearer_caller)],
    ) -> Response:
        del caller
        export = people.export_person(subject)
        _export_audited(export)
        archive = _export_archive(export, app=app, subject=subject, now=admin.clock())
        return Response(
            archive,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="export.zip"'},
        )

    return router
