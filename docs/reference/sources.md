# Sources reference

Audience: an engineer or operator looking up what a source is, what is
accepted, and how it is kept current. Tasks: [add a Git
source](../runbooks/add-a-source.md), [renew a secret](../runbooks/renew-a-source-secret.md),
[call the fetch-now hook](../runbooks/call-the-fetch-now-hook.md).

## The row

A source is a row: an id, a name and a URL that are each unique, the ref it
follows, and the account that owns it. An instance starts with no source; an
admin adds one under Settings · Sources.

- The name is `[a-z0-9][a-z0-9-]{0,63}` and unique. The URL is unique and
  carries no credentials: an `https://` address has no user part at all (the
  token belongs in the Secret field, and the mirror answers a user name of its
  own), and an SSH user is a login name from `[A-Za-z0-9._~-]`.
- Of remote addresses only `https://host[:port]/…`,
  `ssh://[user@]host[:port]/…` and `user@host:path` are an access: the host a
  plain name or an IPv6 literal in brackets, an `ssh://` path free of `%`, `?`,
  `#`, `[` and `]`, an scp path free of `[` and `]`. Every other remote
  spelling, `http://`, a user part on `https://` and any control character
  included, is refused before any secret is used; Check connection gives the
  same refusal. A token in the path or query is not a user part and is not
  inspected.
- The access kind is derived from the URL scheme: `https://` is a token;
  `git@` and `ssh://` are a deploy key. An unknown radio value, a URL that
  names no access, or a mismatch with the chosen radio is refused. A fourth
  kind reads no secret: a bare repository under `/data/local-sources`, added
  with a `file://` or bare-path address, "on this box" as the access kind and
  no secret (one is refused). An address resolved the way git opens one
  (symlink, encoded or literal `..`) must stay under the mount, when added and
  at every fetch.
- HTTPS needs a read-only token and the image's CA certificates. SSH uses the
  owned deploy-key draft Add shows, for registration at the Git host.
  *Check connection* probes that exact token or draft (or path) before
  anything is stored, and *Create* is refused unless that exact check
  succeeded.
- The secret is stored encrypted in the source's row, the only place it stands,
  opened with a key derived from the master key (`PRESENTATOR_SECRET_KEY_FILE`) and read at every
  pull ([ADR 0013](../decisions/0013-secrets-at-rest-and-credential-delivery.md)).
  Keep the master key with the data: an instance restored without it lists its
  decks and can pull none of them, and a source's secret is
  [renewed](../runbooks/renew-a-source-secret.md) if it is unreadable.
  Ciphertext this instance's key cannot open is refused rather than handed to
  `git`: that source's fetch fails and its decks stay listed. A source row
  always carries its encrypted secret; private halves and HTTPS tokens are
  never shown.
- Opening Add with the SSH radio mints this admin an ed25519 deploy key, or
  shows the one already minted for them today, as a `source_key_drafts` row
  carrying the public half in clear and the private half encrypted the same
  way; *Create* binds it to the new source and deletes the draft, and a draft
  nobody creates a source from is swept a day after it was minted.
- The key draft and *Check connection* are also served as JSON to an admin:
  `POST /api/sources/new/key-draft` answers the draft's `id` and public key
  (the private half is in no answer), and `POST /api/sources/new/check` takes
  the URL, the access (`AccessKind`), the token or null and the draft id, and
  answers the probe's `failure`, `commit`, `detail` and `fingerprint`. A
  mismatch between the access and the URL is refused before any probe, as the
  problem `source-refused`.
- *Create* is also served as JSON to an admin: `POST /api/sources` takes the
  check's values (the URL, the access, the token or null, the draft id) with the
  source's `name` and the `fingerprint` of the check that succeeded for exactly
  those values. It answers 201 with the source's `name`, its absolute `hook_url`
  and its `webhook_secret`, and a `Location` naming only the source
  (`/api/sources/<name>`). The webhook secret is in that one answer: nothing
  keeps it, no GET and no log carries it, and a lost answer is recovered by
  renewing it. A refusal is a `source-refused` problem (422) and creates no
  source and delivers or keeps no webhook secret. It does not promise that
  nothing changes: a draft that is not this admin's, or is stale, makes the admin a
  fresh draft before it is refused, and a store that rejects the row at the write (a
  concurrent duplicate) answers `duplicate-name`. A failure after the row is
  written (the first fetch) is a plain 500 with the source created and the
  secret lost, so the person renews it.
- The problem type `source-refused` is the URI
  `tag:hallucinai.de,2026-09-08:presentator/problems/source-refused`, with the
  title `Unprocessable Content`, status 422 and the member `reason`, one of:
  `malformed-name` (not a source name, or the reserved `new`),
  `duplicate-name` (another source has the name), `duplicate-url` (another
  source has the URL), `access-mismatch` (the access is not the one the URL
  names, or the URL names none: a user part, plain `http`), `missing-secret` (HTTPS without a
  token), `not-checked` (no matching successful Check), `credential-not-allowed` (a
  token for SSH or a folder), `outside-local-mount` (a folder outside the
  mount) and `deploy-key-unavailable` (the draft key is gone or not this
  admin's).
  Check answers only `access-mismatch`; Create answers all nine. The date is the one RFC 4151 asks for:
  presentator's commit `37beebc` of 2026-09-08 is the first that names
  `hallucinai.de`, as the operator's hosts `presentator.hallucinai.de` and
  `copresenter.hallucinai.de`; the domain's registration date is not in the
  repository, so the type takes the date that evidence shows.

## Keeping it current

`gitmirror` keeps a bare mirror of each source's repository under
`PRESENTATOR_MIRRORS` by driving `git` as a subprocess
([ADR 0010](../decisions/0010-git-sources-mirror.md)); nothing is ever checked
out, and the tree is read at one commit. A pull runs with a minimal
environment: terminal prompting off, the global and system git configuration
neutralised, any inherited credential helper cleared, and `ssh` in batch mode
with its own connect timeout, so nothing on the machine can turn a pull into a
wait for an answer nobody will give. An SSH fetch writes the private half to a
0600 file in a 0700 directory it removes in a `finally`, and runs `ssh`
against it alone (`IdentitiesOnly=yes`), with the host key pinned on first
contact (`StrictHostKeyChecking=accept-new`) and refused if it ever changes.
The mirror of a local folder runs as `presentator`, uid 1001, so the
repository it opens must be owned by that uid outright, or git refuses it as
"dubious ownership".

The server polls every source every `PRESENTATOR_SOURCE_POLL_SECONDS` on a
task beside the routes, never twice at once, and starts the next tick after the
bound ends a slow one. A pull is bounded by
`PRESENTATOR_SOURCE_TIMEOUT_SECONDS`, so an unreachable source costs one poll
that bound and no more. Opening the deck list reads the database and pulls
nothing. A refresh walks the sources one after another, taking each one in and
building its decks before it reads the next. A source that cannot be read ends
its run and the walk goes on with the next one.

`POST /sources/<name>/fetch` with the source's own secret does the same at
once. It reads no payload, so every git host and a `post-receive` hook are the
same caller. The secret is accepted from `Authorization: Bearer` or
`X-Gitlab-Token`, stored only as a SHA-256 hash, and compared in constant
time. A wrong secret, a missing one, an unknown source, a trailing slash, and
any other path under `/sources/` answer alike (404, empty body). Only that POST
is open; GET and every other method under `/sources/` stay behind the login. A
flood of calls collapses into the one refresh that runs at a time.

## Runs and state

Every poll of a source, reachable or not, and every read of it that fails,
records one run: the moment, whether it reached the source, and either the
commit it found or a typed reason it did not, never the raw error a git
command left behind. The table keeps the newest three runs per source. The
Sources list shows the newest run's state: never fetched; reachable; refused
when the host turns the login down; failed when the host answered with
anything else or its mirror could not be read; unreachable only when the host
never answers; or Error when a credential could not be resolved or sent, never
the internal reason why.

## The source's page

`/settings/sources/<name>` shows state and fetched age, *Fetch now* (which
refreshes that one source), the HTTPS secret as fixed dots with one Renew, or
the SSH deploy-key public half with Copy and direct Renew, the webhook address
with Copy and its own Renew, the newest three runs, and its decks (only those
the viewer owns). SSH Renew replaces the local pair immediately, leaves the
replacement public half ready to register, and neither updates the Git host nor
keeps an old-key overlap; HTTPS renewal takes a new value and shows nothing
back; webhook renewal shows its new value exactly once to the session that
created or renewed it.

On the Sources list, each row's *Fetch now* refreshes only that source and
returns to the list; on the source's page it returns to that page.

The list, a source's page and *Fetch now* are also served as JSON for an admin
(a session or a person's bearer). A signed-in non-admin gets a 403 problem;
a signed-out caller or an invalid bearer meets the sign-in gate's own 401 or
redirect instead:
`GET /api/sources`, `GET /api/sources/<name>` and `POST /api/sources/<name>/fetch`
(204 once that source is taken in and built; an unknown name is a 404 problem).
They carry the state, the stored `fetched_at` instant of the newest run and, on
the page, the absolute `hook_url`, the SSH `public_key`, the newest three runs
(each with its own state, `at`, short `commit` or failure `reason`) and the
viewer's own decks. They carry instants where the Jinja pages word ages, and no
secret: the webhook secret comes back only in the answer to create or renew.

Renewing access is also a JSON write for an admin, with the same refusals:
`POST /api/sources/<name>/access` takes `{"secret": <token or null>}` and answers
204 with no content, never the value. An SSH source renews its own pair
and ignores the secret (the new public half is on the page); an unknown name is
a 404 problem; a blank or null HTTPS token, a token that is not one printable
line (git cannot be handed it) and a local folder are a 422 problem, which says
no reason.

Renewing the webhook secret is a JSON write for an admin too, with the same
refusals: `POST /api/sources/<name>/webhook-secret` takes no body (anything sent
is ignored) and answers 200 with `{"webhook_secret": <the new value>}`, the one
answer that ever carries it. The old secret stops working at once. An unknown
name is a 404 problem. Nothing keeps the value, so an answer that was lost is
recovered by renewing again.

Remove, at the bottom, asks once for a confirm naming the source's decks and
runs, then deletes the source row, its runs, its decks, its mirror, and every
built talk those decks left on disk, so the same URL can be added again
straight after as a new source with a fresh secret. Once a talk was pointed
at, only this takes it off disk.

Removal is also JSON for an admin, in two steps. `GET /api/sources/<name>/removal`
reads only and answers 200 with `{"deck_count", "run_count"}`: every deck row the
source ever carried (each is one deck and its built talk, whoever owns it and
marked removed or not) and the runs it keeps. `DELETE /api/sources/<name>` takes
those two counts as required non-negative query parameters and no body; missing
or malformed counts are the framework's 422 and remove nothing. It removes what
the paragraph above names and answers 204 with no content, after which the name
and the URL can be added again. An unknown name, `new`, a source removed
meanwhile, or a second removal that found it gone is a 404 problem. Every
other answer removes no row:

- counts that no longer match what removal would take (a refresh added a deck
  or a run since, or deleted a deck whose folder was missing past its
  retention) are a `source-removal-stale` problem, 409, with `deck_count`
  and `run_count` as they stand now; confirming again sends those;
- a mirror or a built talk that would not leave the disk is a
  `source-removal-refused` problem, 500, with the member `reason` `disk-leftover`
  and no path. The source, its decks and its runs stay listed and its URL stays
  taken; some talks may already be gone from disk. Repeating the call with the
  same counts finishes it.

The two problem types are the URIs
`tag:hallucinai.de,2026-09-08:presentator/problems/source-removal-stale` (members
`deck_count`, `run_count`) and
`tag:hallucinai.de,2026-09-08:presentator/problems/source-removal-refused`
(member `reason`).
