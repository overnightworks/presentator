# People

Audience: whoever calls or operates the people route. The contract (the body,
the token rules, the order hal follows) is owned by edge's `docs/people.md`;
this page states only what Presentator does with it.

## Erase a person

`DELETE /api/admin/people/{subject}` erases the person whose Keycloak subject
is `{subject}`: their account, sessions, preferences and key draft, and every
source they own with its decks, runs, mirror and builds. `?dry_run=true`
counts and changes nothing. Both answer the `people_erasure` v1 body with
`kinds`, counted by [kind](../../src/presentator/contracts/people.py); the
counts are what was removed in a real run. `affects_others` is always empty.

| Answer | When |
| --- | --- |
| 200 | Erased, previewed, or the subject is unknown (zero counts, no row made). |
| 401 | No token, or any verification failure. |
| 403 | A valid token without the admin group or a group below it. |
| 409 | A refresh or build holds the lock for longer than 60 seconds; nothing was deleted. `Retry-After` is the wait in whole seconds, and `busy` lists the running work. hal's timeout must exceed 60 seconds. |
| 422 | `dry_run` is not a boolean; nothing was deleted. |
| 500 | A mirror or build directory could not be removed; the body is the 200's plus `leftover` (below). The person remains and a repeat finishes it. A plain 500 without `leftover` is a failed step (below). |

Every answer carries `Cache-Control: no-store`. A request never creates an
account.

### What is left

A real run tries every source the person owns, even after one could not be
removed. A source whose mirror or build directory stays keeps its rows and the
person keeps the account; every other source is still removed. The 500 then
carries `dry_run` `false`, `kinds` (what this attempt removed: the account and
a source that stayed count zero), `affects_others` `[]` and `leftover`: one
entry per directory that stayed, `{kind, count, path}` with `count` 1. `kind`
is `mirrors` or `builds`; `path` is relative to the data volume, as
`mirrors/<64 hex digits>.git` or `builds/<run>`, the first part being the
configured directory's own name. A path never holds a source name, an address,
a deck, a commit, an email or file content; a mirror's hex name is the SHA-256
of the source address, so it can be checked against a guessed address. The
`mirrors` count in `kinds` counts source rows, not mirror directories on disk.

A stored build that lies where no run can be placed (a corrupted row or a
changed builds root) has no directory to name: the run still tries every other
source, keeps the account, and fails with a plain 500. No `person erase:` line
is written for that call; the server logs the exception with the source's
internal id. A dry run never lists a leftover.

The log line of a leftover holds the kinds and counts left over and removed,
never a path or a name; the mirror's own warning names the source's internal id.

## Export a person

`GET /api/admin/people/{subject}/export` answers a ZIP
(`application/zip`, a file name that names no subject) whose root holds
`export.json` and nothing else. It is the `people_export` v1 body: `app`,
`subject`, `exported_at` (UTC, `Z`, whole seconds), `data`, `files` and
`withheld`. `data` holds every row keyed by the person (account, sessions,
preferences, key draft, sources with their decks and runs) as the
[export records](../../src/presentator/contracts/people.py) show them; `files`
is always empty, because builds and mirrors derive from git.

The secrets are never read into it. `withheld` counts each kind left out under
its own word: `source-credential`, `hook-secret-hash`, `key-draft-private-key`,
`refresh-token` and `id-token`, each with `reason` `secret`. A source address is
exported as stored: the guarantee is that its authority carries no credential part; a
secret typed into a path or query is exported as typed.

An unknown subject, and a person who owns no row, answers 200 with `data`
exactly `{}`, `files` `[]` and `withheld` `[]`; no account is made. The answers
401 and 403 are the erase route's, and every answer carries
`Cache-Control: no-store`. The log line holds the withheld counts and names
nobody.

## Who may call

Only an access token of the instance's Keycloak client, with that client as
its one audience, type `at+jwt`, an `azp` listed in
`PRESENTATOR_TOKEN_CLIENTS` and the admin group or a subgroup. `webauth`
checks it; the cookie counts for nothing. With the setting empty (the
default) neither the gate nor the route exists. The same setting opens a
person's own `/api/` to their token (see [sign-in and
session](sign-in-and-session.md)).

## What is logged

One line per call: the operation, the outcome and the counts. It names neither
the caller nor the person, and the server writes no access log.

## Known gap

The erasure removes every run its decks deliver from. Superseded builds and
leftovers go at the end of the next refresh pass ([builds](builds.md)), so a
leftover can outlive an erasure by one pass, or longer while that pass refuses.
The live instance keeps the setting empty until the
operator releases it.
