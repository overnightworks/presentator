"""Settings · Sources: the list and the removal of one source.

Admin only, refused exactly as Settings · General is. No access secret appears
here. A source's own page, the renewals and adding a source are the lobby app's.
"""

from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Form, Request, Response
from starlette.responses import RedirectResponse

from presentator.api.admission import gated_person, gated_session_id
from presentator.api.pages import Pages
from presentator.api.preferences import SETTINGS
from presentator.application.decks import (
    Decks,
    SourceRemovalRefused,
    SourceRemovalStale,
)
from presentator.contracts.decks import (
    AccessKind,
    SourceRemoval,
    SourceState,
)
from presentator.contracts.text import LobbyText

SOURCES: Final = f"{SETTINGS}/sources"
REMOVAL_TEMPLATE: Final = "source_remove.html"
FETCH: Final = f"{SOURCES}/{{name}}/fetch"
REMOVE: Final = f"{SOURCES}/{{name}}/remove"
REMOVE_CONFIRMED: Final = f"{SOURCES}/{{name}}/remove/confirmed"


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceRow:
    """One source as the list renders it: state, name, URL, access, fetched age."""

    name: str
    url: str
    access: str | None
    state: str
    state_word: str
    fetched: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceRemovalView:
    """The confirm page's read model: the source's name, one sentence, its counts.

    The counts travel back with the confirm's own form (R1): a person's yes
    is good for exactly the numbers they saw, never a later one silently
    read fresh at the moment their yes is spent.
    """

    name: str
    title: str
    body: str
    deck_count: int
    run_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class _Surfaces:
    """The Sources pages: the list, Fetch now and removal."""

    pages: Pages
    decks: Decks
    # A one-time notice of what a removal took with it, shown once on the
    # next visit to the list; one session ever holds one, so its key is the
    # session alone.
    _removed_once: dict[str, str] = field(default_factory=dict[str, str])

    def sources_page(self, request: Request) -> Response:
        """Show each source and what its newest run said, to an admin."""
        if not gated_person(request).is_admin:
            return _refused()
        text = self.pages.appearance(request).text
        return self.pages.page(
            request,
            "sources.html",
            sources=self._rows(text),
            removed=self._removed_once.pop(gated_session_id(request), None),
        )

    def fetch_now(
        self,
        request: Request,
        name: str,
    ) -> Response:
        """Refresh that one source and return to the list."""
        if not gated_person(request).is_admin:
            return _refused()
        if not self.decks.refresh_named(name):
            return Response(status_code=HTTPStatus.NOT_FOUND)
        return RedirectResponse(SOURCES, status_code=HTTPStatus.SEE_OTHER)

    def ask_removal(self, request: Request, name: str) -> Response:
        """Show what removing this source would take with it. Deletes nothing."""
        if not gated_person(request).is_admin:
            return _refused()
        removal = self.decks.removal(name)
        if removal is None:
            return Response(status_code=HTTPStatus.NOT_FOUND)
        text = self.pages.appearance(request).text
        return self.pages.page(
            request,
            REMOVAL_TEMPLATE,
            removal=_removal_view(removal, text),
        )

    def confirm_removal(
        self,
        request: Request,
        name: str,
        deck_count: Annotated[int, Form()] = -1,
        run_count: Annotated[int, Form()] = -1,
    ) -> Response:
        """Delete the source and everything only it owned, then say what went.

        The counts a person confirmed travel back in the form; a mismatch at
        the moment this runs is not deleted, it shows the confirm again with
        the numbers as they stand now. A mirror or a build that would not go
        stops this too, before any row is touched, and the re-rendered removal
        page says so.
        """
        if not gated_person(request).is_admin:
            return _refused()
        outcome = self.decks.remove_source(
            name,
            expected_deck_count=deck_count,
            expected_run_count=run_count,
        )
        if outcome is None:
            return Response(status_code=HTTPStatus.NOT_FOUND)
        text = self.pages.appearance(request).text
        if isinstance(outcome, SourceRemovalStale):
            return self.pages.page(
                request,
                REMOVAL_TEMPLATE,
                removal=_removal_view(outcome.current, text),
            )
        if isinstance(outcome, SourceRemovalRefused):
            removal = self.decks.removal(name)
            if removal is None:
                return Response(status_code=HTTPStatus.NOT_FOUND)
            return self.pages.page(
                request,
                REMOVAL_TEMPLATE,
                removal=_removal_view(removal, text),
                refused=text.source_remove_refused,
            )
        self._removed_once[gated_session_id(request)] = _removed_sentence(outcome, text)
        return RedirectResponse(SOURCES, status_code=HTTPStatus.SEE_OTHER)

    def _rows(self, text: LobbyText) -> tuple[SourceRow, ...]:
        return tuple(
            SourceRow(
                name=source.name,
                url=source.url,
                access=_access_word(source.access, text),
                state=source.state.value,
                state_word=_state_word(source.state, text),
                fetched=(
                    None
                    if source.age is None
                    else self.pages.age_in_words(source.age, text.language_tag)
                ),
            )
            for source in self.decks.listed_sources()
        )


def source_routes(*, pages: Pages, decks: Decks) -> APIRouter:
    """The Sources addresses, for the lobby factory to include."""
    surfaces = _Surfaces(pages=pages, decks=decks)
    router = APIRouter()
    router.add_api_route(SOURCES, surfaces.sources_page, methods=["GET"])
    router.add_api_route(FETCH, surfaces.fetch_now, methods=["POST"])
    router.add_api_route(REMOVE, surfaces.ask_removal, methods=["POST"])
    router.add_api_route(REMOVE_CONFIRMED, surfaces.confirm_removal, methods=["POST"])
    return router


def _removal_view(removal: SourceRemoval, text: LobbyText) -> SourceRemovalView:
    """The confirm page's read model, its counts already worded into a sentence."""
    return SourceRemovalView(
        name=removal.name,
        title=text.source_remove_confirm_title.format(name=removal.name),
        body=text.source_remove_confirm_body.format(
            decks=removal.deck_count,
            deck_word=_counted(
                removal.deck_count,
                singular=text.source_remove_deck_singular,
                plural=text.source_remove_deck_plural,
            ),
            talk_word=_counted(
                removal.deck_count,
                singular=text.source_remove_talk_singular,
                plural=text.source_remove_talk_plural,
            ),
            runs=removal.run_count,
            run_word=_counted(
                removal.run_count,
                singular=text.source_remove_run_singular,
                plural=text.source_remove_run_plural,
            ),
        ),
        deck_count=removal.deck_count,
        run_count=removal.run_count,
    )


def _removed_sentence(removal: SourceRemoval, text: LobbyText) -> str:
    """The list's one line naming what a finished removal took with it."""
    return text.source_removed.format(
        name=removal.name,
        decks=removal.deck_count,
        deck_word=_counted(
            removal.deck_count,
            singular=text.source_remove_deck_singular,
            plural=text.source_remove_deck_plural,
        ),
        runs=removal.run_count,
        run_word=_counted(
            removal.run_count,
            singular=text.source_remove_run_singular,
            plural=text.source_remove_run_plural,
        ),
    )


def _counted(count: int, *, singular: str, plural: str) -> str:
    """The catalog's word for this count: one word or the other, never a rule."""
    return singular if count == 1 else plural


def _state_word(state: SourceState, text: LobbyText) -> str:
    """The catalog's own word for what a source is, shown with its shape."""
    return {
        SourceState.REACHABLE: text.source_state_reachable,
        SourceState.ERROR: text.source_state_error,
        SourceState.REFUSED: text.source_state_refused,
        SourceState.FAILED: text.source_state_failed,
        SourceState.NEVER_FETCHED: text.source_state_never_fetched,
    }[state]


def _access_word(kind: AccessKind | None, text: LobbyText) -> str | None:
    """The catalog's tag for how this source is read, when the URL names one."""
    if kind is AccessKind.HTTPS:
        return text.source_access_token
    if kind is AccessKind.SSH:
        return text.source_access_deploy_key
    if kind is AccessKind.FILE:
        return text.source_access_local
    return None


def _refused() -> Response:
    """Sources is admin only, so a person without the role is turned away."""
    return Response(status_code=HTTPStatus.FORBIDDEN)
