"""Settings · Sources: the list and the page of one source.

Admin only, refused exactly as Settings · General is. No access secret appears
here. The webhook secret appears after a renewal exactly once; adding a source,
which shows its secret in the Add page's own memory, is the lobby app's.
"""

from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Form, Request, Response
from starlette.responses import RedirectResponse

from presentator.api.admission import gated_person, gated_session_id
from presentator.api.hooks import hook_url
from presentator.api.pages import Pages
from presentator.api.preferences import SETTINGS
from presentator.application.decks import (
    Decks,
    SourceRemovalRefused,
    SourceRemovalStale,
)
from presentator.contracts.decks import (
    AccessKind,
    ShownSourceRun,
    SourcePage,
    SourceRemoval,
    SourceRunFailure,
    SourceRunOutcome,
    SourceState,
)
from presentator.contracts.text import LobbyText

SOURCES: Final = f"{SETTINGS}/sources"
NEW: Final = f"{SOURCES}/new"
PAGE: Final = f"{SOURCES}/{{name}}"
FETCH: Final = f"{SOURCES}/{{name}}/fetch"
ACCESS: Final = f"{SOURCES}/{{name}}/access"
WEBHOOK: Final = f"{SOURCES}/{{name}}/webhook"
REMOVE: Final = f"{SOURCES}/{{name}}/remove"
REMOVE_CONFIRMED: Final = f"{SOURCES}/{{name}}/remove/confirmed"
_SOURCE_PAGE: Final = "source.html"
_STAY_ON_PAGE: Final = "page"


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
class SourceRunRow:
    """One recent poll as the source page renders it."""

    state: str
    state_word: str
    fetched: str
    commit: str | None
    reason: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceDeckRow:
    """One deck chip on the source page."""

    slug: str
    title: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceView:
    """The source page's read model, with words already chosen."""

    name: str
    url: str
    access: str | None
    state: str
    state_word: str
    fetched: str | None
    address: str
    webhook_secret: str | None
    webhook_secret_held_elsewhere: bool
    # A source on this box carries no secret at all, so its page offers
    # neither the dots nor Renew: there is nothing there to renew.
    carries_no_secret: bool
    # The SSH URL kind owns the direct pair replacement control. Its presence
    # does not depend on a stored public half, which is data rather than a UI state.
    renews_deploy_key: bool
    # A deploy key's public half, shown in clear beside the tag instead of
    # the dots — nothing for a source that carries an HTTPS token instead.
    public_key: str | None
    runs: tuple[SourceRunRow, ...]
    decks: tuple[SourceDeckRow, ...]


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
class _ShownOnceKey:
    """A one-time webhook secret, owned by one session."""

    session_id: str
    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class _Surfaces:
    """The Sources pages: the list, one source's page, its renewals and removal."""

    pages: Pages
    decks: Decks
    _shown_once: dict[_ShownOnceKey, str] = field(
        default_factory=dict[_ShownOnceKey, str]
    )
    # A one-time notice of what a removal took with it, shown once on the
    # next visit to the list the same way the webhook secret above is shown
    # once on the next visit to the source's own page; one session ever holds
    # one, so its key is the session alone.
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

    def source_page(self, request: Request, name: str) -> Response:
        """Show the source's own page; a renewed webhook secret shows once."""
        if not gated_person(request).is_admin:
            return _refused()
        shown = self.decks.shown_source(name, viewer=gated_person(request).id)
        if shown is None:
            return Response(status_code=HTTPStatus.NOT_FOUND)
        session_id = gated_session_id(request)
        renewed = _ShownOnceKey(session_id=session_id, name=name)
        secret = self._shown_once.pop(renewed, None)
        held_elsewhere = _held_elsewhere(self._shown_once, session_id, name)
        text = self.pages.appearance(request).text
        return self.pages.page(
            request,
            _SOURCE_PAGE,
            source=self._view(
                request,
                shown,
                text,
                webhook_secret=secret,
                webhook_secret_held_elsewhere=held_elsewhere,
            ),
        )

    def fetch_now(
        self,
        request: Request,
        name: str,
        stay: Annotated[str, Form()] = "",
    ) -> Response:
        """Refresh that one source and return to the list, or to its page."""
        if not gated_person(request).is_admin:
            return _refused()
        if not self.decks.refresh_named(name):
            return Response(status_code=HTTPStatus.NOT_FOUND)
        destination = f"{SOURCES}/{name}" if stay == _STAY_ON_PAGE else SOURCES
        return RedirectResponse(destination, status_code=HTTPStatus.SEE_OTHER)

    def renew_access(
        self,
        request: Request,
        name: str,
        secret: Annotated[str, Form()] = "",
    ) -> Response:
        """Take a new access secret and show nothing of it back."""
        if not gated_person(request).is_admin:
            return _refused()
        shown = self.decks.shown_source(name, viewer=gated_person(request).id)
        if shown is None:
            return Response(status_code=HTTPStatus.NOT_FOUND)
        if not self.decks.renew_access(name, secret):
            text = self.pages.appearance(request).text
            return self.pages.page(
                request,
                _SOURCE_PAGE,
                source=self._view(request, shown, text, webhook_secret=None),
                refused=text.source_refused_secret,
            )
        return RedirectResponse(f"{SOURCES}/{name}", status_code=HTTPStatus.SEE_OTHER)

    def renew_webhook(self, request: Request, name: str) -> Response:
        """Mint a new webhook secret and show it exactly once on the next GET."""
        if not gated_person(request).is_admin:
            return _refused()
        minted = self.decks.renew_webhook(name)
        if minted is None:
            return Response(status_code=HTTPStatus.NOT_FOUND)
        self._shown_once[
            _ShownOnceKey(session_id=gated_session_id(request), name=name)
        ] = minted
        return RedirectResponse(f"{SOURCES}/{name}", status_code=HTTPStatus.SEE_OTHER)

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
            "source_remove.html",
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
        stops this too, before any row is touched, and the source's own page
        says so.
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
                "source_remove.html",
                removal=_removal_view(outcome.current, text),
            )
        if isinstance(outcome, SourceRemovalRefused):
            shown = self.decks.shown_source(name, viewer=gated_person(request).id)
            if shown is None:
                return Response(status_code=HTTPStatus.NOT_FOUND)
            return self.pages.page(
                request,
                _SOURCE_PAGE,
                source=self._view(request, shown, text, webhook_secret=None),
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

    def _view(
        self,
        request: Request,
        shown: SourcePage,
        text: LobbyText,
        *,
        webhook_secret: str | None,
        webhook_secret_held_elsewhere: bool = False,
    ) -> SourceView:
        return SourceView(
            name=shown.name,
            url=shown.url,
            access=_access_word(shown.access, text),
            state=shown.state.value,
            state_word=_state_word(shown.state, text),
            fetched=(
                None
                if shown.age is None
                else self.pages.age_in_words(shown.age, text.language_tag)
            ),
            address=hook_url(request, shown.name),
            webhook_secret=webhook_secret,
            webhook_secret_held_elsewhere=webhook_secret_held_elsewhere,
            carries_no_secret=shown.access is AccessKind.FILE,
            renews_deploy_key=shown.access is AccessKind.SSH,
            public_key=shown.public_key,
            runs=tuple(self._run_row(run, text) for run in shown.runs),
            decks=tuple(
                SourceDeckRow(slug=deck.slug, title=deck.title) for deck in shown.decks
            ),
        )

    def _run_row(self, run: ShownSourceRun, text: LobbyText) -> SourceRunRow:
        state = run.state
        return SourceRunRow(
            state=state.value,
            state_word=(
                text.source_run_fetched
                if run.outcome is SourceRunOutcome.SUCCESS
                else _state_word(state, text)
            ),
            fetched=self.pages.age_in_words(run.age, text.language_tag),
            commit=run.commit,
            reason=None if run.reason is None else _run_reason(run.reason, text),
        )


def source_routes(*, pages: Pages, decks: Decks) -> APIRouter:
    """The Sources addresses, for the lobby factory to include."""
    surfaces = _Surfaces(pages=pages, decks=decks)
    router = APIRouter()
    router.add_api_route(SOURCES, surfaces.sources_page, methods=["GET"])
    router.add_api_route(FETCH, surfaces.fetch_now, methods=["POST"])
    router.add_api_route(ACCESS, surfaces.renew_access, methods=["POST"])
    router.add_api_route(WEBHOOK, surfaces.renew_webhook, methods=["POST"])
    router.add_api_route(REMOVE, surfaces.ask_removal, methods=["POST"])
    router.add_api_route(REMOVE_CONFIRMED, surfaces.confirm_removal, methods=["POST"])
    router.add_api_route(PAGE, surfaces.source_page, methods=["GET"])
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


def _run_reason(reason: SourceRunFailure, text: LobbyText) -> str:
    """The catalog's sentence for why that poll failed."""
    return {
        SourceRunFailure.UNREACHABLE: text.source_run_unreachable,
        SourceRunFailure.CREDENTIAL_UNRESOLVABLE: text.source_run_secret,
        SourceRunFailure.REFUSED: text.source_run_refused,
        SourceRunFailure.FAILED: text.source_run_failed,
    }[reason]


def _held_elsewhere(
    shown_once: dict[_ShownOnceKey, str],
    session_id: str,
    name: str,
) -> bool:
    """Whether another session still holds a one-time secret for this source."""
    return any(key.name == name and key.session_id != session_id for key in shown_once)


def _refused() -> Response:
    """Sources is admin only, so a person without the role is turned away."""
    return Response(status_code=HTTPStatus.FORBIDDEN)
