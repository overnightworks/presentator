# ADR 0020: The lobby is a JSON API under `/api/`, and the pages read it

Audience: humans and agents adding a route under `/api/`, or a page that reads
the lobby as data.

- Status: ACCEPTED 2026-10-29 — amends [ADR 0012](0012-themes-and-language.md)
  (the catalog is also served as data, not only rendered into HTML)
- Date: 2026-10-29
- Decision authority: rulings E1 to E7 of
  [#185](https://github.com/overnightworks/presentator/issues/185), ruled
  2026-10-29; slice A1 is [#623](https://github.com/overnightworks/presentator/issues/623)
- Neighbours: [ADR 0003](0003-libraries-for-models-and-auth.md) owns the
  sign-in mechanics; the [sign-in reference](../reference/sign-in-and-session.md)
  owns what holds today

## Context

The first SvelteKit page needs who reads, in which words and theme, and where
sign-in and sign-out are. Until now these existed only inside Jinja rendering.

## Decision

- The JSON API lives under `/api/`, outside `/api/admin/`, which stays the
  bearer-only machine boundary of [people](../reference/people.md).
- The session gate guards `/api/` like every other path. A path that must answer
  a signed-out caller is named as one exact optional path; there is no prefix
  and no catch-all. `GET /api/session` is the first.
- A person's hal access token opens `/api/` as that person, as webauth's person
  paths define it; a bearer beside a cookie wins, and an invalid bearer is
  refused, never answered from the cookie. This holds while
  `PRESENTATOR_TOKEN_CLIENTS` names a client; with it empty no bearer gate is installed and a bearer on
  `/api/` is ignored, as the sign-in reference states.
- JSON writes rely on webauth's origin check (`Sec-Fetch-Site` first, then
  `Origin`) and carry no CSRF token; a write admitted by a bearer skips that
  check.
- The wire is typed: every answer of a route is a model with named fields, not
  a dict. The library's refusals are not routes.
- The catalog is served as data: `words` is the resolved `LobbyText` of the
  reader's language, so a page holds no strings of its own.
- Presentator's own errors on the JSON routes under `/api/` follow RFC 9457
  problem details (`application/problem+json`; `api/problems.py`), from
  [#185](https://github.com/overnightworks/presentator/issues/185) A3 on. The
  type is `about:blank` and the title the HTTP reason phrase, so presentator
  words nothing and a client words the status from its own catalog. The
  library's refusals keep their shape: webauth answers the gate's 401 and a CSRF
  refusal in its own format, `/api/admin/`, the co-presenter, the fetch hook and
  the talk host keep theirs, the framework's own 404 and 405 stay as they are,
  and a store failure is its plain 500. The framework's validation 422 keeps
  its status, media type and `detail` shape on every lobby route, `/api/admin/`
  and the form posts included, but each error is reduced to `loc`, `msg` and
  `type`, so no answer repeats what the request carried. Pydantic's own `msg` and
  `loc` can still carry a value for a discriminated-union tag, a `dict` key and
  UUID parsing, so a request model that takes a secret must not use those; the
  talk host keeps the framework's default.
- OpenAPI is generated from the models and not served: `openapi_url` stays
  `None`.

## Alternatives rejected

- A catalog owned by the frontend: two sources of words, one of them outside
  the language and preference rules of ADR 0012.
- Keeping every page server-rendered: the first SvelteKit page would still need
  who reads and in which words, so the data would be fetched anyway.

## Consequences

A route under `/api/` needs a decision here only when it asks for another
optional path. A store failure while resolving a reader is a 500, never a
signed-out answer.
