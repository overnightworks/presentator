# ADR 0021: The lobby is a SvelteKit app served by FastAPI under the strict CSP

Audience: humans and agents building a lobby page, or changing how the lobby is
served or what its security policy allows.

- Status: ACCEPTED 2026-10-31 — supersedes
  [ADR 0008](0008-lobby-server-rendered.md); amends
  [ADR 0012](0012-themes-and-language.md) (the look comes from appkit)
- Date: 2026-10-31
- Decision authority: the operator's rulings on
  [#185](https://github.com/overnightworks/presentator/issues/185) (E1 to E4,
  29.10.2026 and 31.10.2026) and on
  [#683](https://github.com/overnightworks/presentator/issues/683) (the
  announcer's exception, A, 31.10.2026); slice F1a is #683
- Neighbours: [ADR 0020](0020-the-lobby-is-a-json-api.md) owns the JSON API the
  pages read; [ADR 0016](0016-a-talk-host-signs-in-by-itself.md) and
  [ADR 0019](0019-presentator-is-an-edge-app.md) own the one process and the one
  image this record stays inside

## Context

The lobby grows pages that change in place: a theme or a language that reaches
every page at once, a secret shown exactly once, and the family look the other
overnightworks apps share through appkit's Svelte components. Server-rendered
Jinja with htmx (ADR 0008) cannot take appkit's components, and every new
interaction became a template, a fragment and a form route.

## Decision

- The lobby is a SvelteKit app with Svelte 5 in `frontend/`, built by
  `adapter-static` into a single-page shell with `ssr = false`, as songmaker's
  frontend is.
- The one FastAPI process serves it. The host reads the built shell once at
  start, hashes its one inline bootstrap script, and fills `<html lang
  data-theme>` per request from the person's stored preference, so the first
  paint has the right theme without device storage. No Node web service runs;
  Node stays in the image for Slidev only.
- The shell answers a browser's page load on any lobby address no route
  claims, so the app's router decides it, a not-found page included. It takes
  the router's not-found place rather than a catch-all route, so a route's own
  404 and a wrong method's 405 stay the framework's. `/api/`, `/_app/`,
  `/auth/`, the hook prefix, `/static/`, `/login/oidc`, `/logout`, `/health`
  and the retired `/setup` never fall back to it: a missing asset or API is
  still a 404. `/_app/` is public and serves the build's assets. A page not yet
  moved stays a Jinja page, and a link to it carries `data-sveltekit-reload`;
  a Jinja page's link back to the app (the Sources list's Add) is an ordinary
  document link.
  `/deck/{slug}/pdf` stays a server download; for a deck that is missing,
  foreign or unknown, or whose file is gone, it answers the shell under 404,
  so the app shows the root not-found board. That shell is the route's own
  refusal, never a fallback for an unmatched address. `/deck/{slug}/` has no
  redirect: a page load there gets the shell, and the router settles on
  `/deck/{slug}`.
- The root load reads the session once; the `(lobby)` route group holds the
  header and footer, so `/login` and the refusals stay headerless. Home, at
  `/`, is the first page in the group; the deck page at `/deck/{slug}` is the
  second, and it draws its own unknown-deck board for a 404. Settings General,
  Add source (`/settings/sources/new`) and a source's own page
  (`/settings/sources/<name>`) are the pages below them. A 401 from any read
  or write leaves the app by a document navigation to the sign-in with the
  current address as `next`, and its promise never settles, so no error page
  shows on the way out. A load names its own `url` as `next`, because it runs
  before the address commits.
- A 404 or 403 from the API becomes SvelteKit's `error()` with its status, and
  any other refusal fails loud. The root `+error.svelte` shows the lobby's
  header and the unknown-address words, answered at 200 by the shell. There is
  no catch-all route, so an address no route claims still reaches the server
  by a page load. The header is one component for the `(lobby)` group and the
  error page; `aria-current="page"` follows the section of the address (Decks on `/` and
  on a deck page, `/deck/<one segment>` with an optional trailing slash and no
  deeper address, Settings on `/settings` and below, none elsewhere).
- The error page picks its words by status: 403 shows the for-admins words, 404
  the unknown-address words, and any other status shows the header and the
  status number with no sentence, because the catalog has no general failure
  words. A member at `/settings` meets the 403 board through the real path: the
  shell answers 200 and the API's 403 becomes `error(403)`. The board carries
  its own header and no settings tab strip, which the layout below the error
  boundary draws.
- Settings has its own layout with the tab strip, and its tabs are
  conditional: General is current at `/settings` only, Sources under
  `/settings/sources`. General is a Svelte page; Sources links to its Jinja
  page by a document navigation until it moves, as do the Add and name links
  of the list.
- Add source keeps the one display of the webhook secret in its own component
  state. Its load reads the admin's key draft with a POST (idempotent per
  owner, so a member's 403 becomes the for-admins board); Check and Create go
  through the typed client, and a 422 `source-refused` is the client's
  `Refused`, worded by the catalog. A create answer replaces the form in the
  same document, and the bar takes the source's address by `goto` with
  `shallow` and `replace`: no new history entry, empty page state. Kit keeps
  `page.url` at the Add address and records that Add address as the entry's
  `pageUrl` while the browser's own address is the source's, so Back to that
  entry loads the Add route under the source's address. The Add page checks
  `page.shallow` once, at mount and never reactively (a reactive check would
  destroy the display right after a create), and replaces itself by the source's
  page. The secret is in no URL, no storage, no title and no later GET. On
  `pagehide` the page forgets that secret and the typed one, so a page the
  browser keeps and shows again shows the empty form; `visibilitychange` would
  also drop it from a person who switches tabs to paste it. Chromium keeps no
  lobby page in its back/forward cache (every answer is `no-store` and the page
  makes requests of its own), so the drive cannot restore one; the `pagehide`
  handler is driven by dispatching the event, and the cache clause is held by
  that construction. The drive's cache cell restores a control page first and is
  skipped, with the browser's reasons, while it cannot.
- A source's page reads `GET /api/sources/<name>`, which carries no secret,
  and renews through the typed client. A renewed webhook secret lives in the
  page's `renewed` state and reaches the DOM only as the copy control's value:
  it is in no load data, URL, title, storage, console call or later GET. The
  server answers the shell at the address, so the document GET never carries
  it either. On `pagehide` the page forgets the secret, the typed token and the
  refusal; the drive's cache cell is skipped with the browser's reasons while
  Chromium keeps no lobby page, and the cache clause is held by that
  construction. Removal stays the Jinja confirm until its dialog replaces it.
- A form's save writes through the typed client, then re-reads the session so
  the page repaints in its own document; the effective look belongs to the
  server.
- The look comes from appkit's npm tarball, pinned by release URL in
  `frontend/package.json` with the lockfile's integrity, and imported from
  `node_modules`; presentator adds only its colour in the app slot.
- Pages read the lobby through the typed client: wire types generated by
  `openapi-typescript` from the OpenAPI document of the JSON routes, every call
  sent with `Accept: application/json`.
- The browser keeps the session cookie and SvelteKit's own navigation state in
  `sessionStorage` (scroll positions per history entry), nothing else; lint
  forbids a storage call and a page `snapshot` export.

### The content security policy

The lobby's policy is webauth's default with two additions, both owned by
`content_security_policy()` in `src/presentator/api/auth.py`:

- `script-src` adds the bootstrap's hash, derived from the built shell when the
  host starts. The hash changes with every build, so it is never copied.
- `style-src` adds `'unsafe-hashes'` and the sha256 of the style attribute
  SvelteKit's accessibility announcer is written with. No other style
  attribute, no `'unsafe-inline'` and no foreign host is allowed. The browser
  drive fails on any CSP violation, so a changed announcer style is red in CI.

This exception goes when SvelteKit writes the announcer without an inline
style ([sveltejs/kit#11747](https://github.com/sveltejs/kit/issues/11747)).
The talk host's policy is not touched.

## Consequences

- The lobby has a build step and its own pnpm toolchain, apart from Slidev's.
  The image builds it in its own stage, and only its output enters the
  instance layer; the deck toolchain stage gets nothing of it.
- A first load needs JavaScript. For a private lobby with a handful of pages
  that is accepted; a page that must work without it is not planned.
- Old and new pages stand side by side until the last Jinja page moves; each
  page slice deletes the route, template and tests it replaces.
- Instance defaults are one store per served lobby, so a drive spec that writes
  them is named `*.instance.spec.ts` and runs on a stack of its own per browser;
  the other specs share one stack and never write them.
- The catalog stays the server's: `/api/session` serves it as data, and the
  catalog-purity check holds the Svelte pages to it. A kit component's own
  words are the kit's.

## Rejected alternatives

- **SvelteKit SSR on a Node service beside FastAPI.** Server-rendered first
  paint, but a second runtime service, a proxy and cookie integration, and no
  documented need for a private lobby.
- **Prerendering.** It fits public pages only; the person's words, theme and
  data stay dynamic.
- **Keeping Jinja.** It cannot use appkit's components, against the ruling.
- **Rewriting the announcer at build time instead of the CSP exception.** A
  `preprocess` hook that drops the attribute from SvelteKit's generated root
  drove clean, but it couples the build to the framework's internal file and
  markup and is in effect a framework patch; the operator chose the exception
  (#683, A). Upstream declined moving the style into a stylesheet in favour of
  a hash.
