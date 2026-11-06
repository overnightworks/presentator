# People

Audience: whoever calls or operates the people route, above all hal. The contract (the body,
the token rules, the order hal follows) is owned by edge's `docs/people.md`;
this page states only what Presentator does with it. Each rule carries the ID
that a test cites.

## Erase a person

`DELETE /api/admin/people/{subject}` erases the person whose Keycloak subject
is `{subject}`. `?dry_run=true` counts and changes nothing.

- **PE1** Erasure takes every row of the person: their account, sessions,
  preferences and key draft, and every source they own with its decks, runs,
  mirror and builds. It takes only the named person's rows.
- **PE2** The counts of a real run are what was removed. A dry run changes
  nothing and answers the real run's counts with `dry_run` true. Both answer
  the `people_erasure` v1 body with `kinds`, counted by
  [kind](../src/presentator/contracts/people.py); `affects_others` is always
  empty.

| ID | Answer | When |
| --- | --- | --- |
| PE3 | 200 | Erased, previewed, or the subject is unknown (zero counts, no row made). A repeat answers zero. |
| PE4 | 401 | No token, or any verification failure. |
| PE5 | 403 | A valid token without the admin group or a group below it. |
| PE6 | 409 | A refresh or build holds the lock for longer than 60 seconds; nothing was deleted. `Retry-After` is the wait in whole seconds, and `busy` lists the running work. hal's timeout must exceed 60 seconds. |
| PE7 | 422 | `dry_run` is not a boolean; nothing was deleted. |
| PE8 | 500 | A mirror or build directory could not be removed; the body is the 200's plus `leftover` (below). The person remains and a repeat finishes it. A plain 500 without `leftover` is a failed step (below). |

- **PE9** Every answer carries `Cache-Control: no-store`.
- **PE10** A request never creates an account.

### What is left

- **PE11** A real run tries every source the person owns, even after one could
  not be removed. A source whose mirror or build directory stays keeps its rows
  and the person keeps the account; every other source is still removed.
- **PE12** The leftover 500 carries `dry_run` `false`, `kinds`,
  `affects_others` `[]` and `leftover`. `kinds` holds what this attempt
  removed: the account and a source that stayed count zero. The `mirrors`
  count counts source rows, not mirror directories on disk.
- **PE13** `leftover` names only directories, one entry per directory that
  stayed: `{kind, count, path}` with `count` 1. `kind` is `mirrors` or
  `builds`.
- **PE14** `path` is relative to the data volume, as `mirrors/<64 hex digits>.git`
  or `builds/<run>`, the first part being the configured directory's own name.
  A path never holds a source name, an address, a deck, a commit, an email or
  file content. A mirror's hex name is the SHA-256 of the source address, so it
  can be checked against a guessed address.
- **PE15** A stored build that lies where no run can be placed (a corrupted row
  or a changed builds root) has no directory to name: the run still tries every
  other source, keeps the account, and fails with a plain 500. No
  `person erase:` line is written for that call; the server logs the exception
  with the source's internal id.
- **PE16** The log line of a leftover holds the kinds and counts left over and
  removed, never a path or a name; the mirror's own warning names the source's
  internal id.
- **PE17** A dry run never lists a leftover.

## Export a person

`GET /api/admin/people/{subject}/export` answers a ZIP.

- **PE18** The ZIP is `application/zip` holding the `people_export` v1 body, its
  file name names no subject, and its root holds `export.json` and nothing
  else. The body holds `app`, `subject`, `exported_at` (UTC, `Z`, whole
  seconds), `data`, `files` and `withheld`.
- **PE19** `data` holds every row keyed by the person (account, sessions,
  preferences, key draft, sources with their decks and runs) and only those, as
  the [export records](../src/presentator/contracts/people.py) show them.
- **PE20** `files` is always empty, because builds and mirrors derive from git.
- **PE21** The secrets are never read into the export. `withheld` counts each
  kind left out under its own word: `source-credential`, `hook-secret-hash`,
  `key-draft-private-key`, `refresh-token` and `id-token`, each with `reason`
  `secret`.
- **PE22** The ID token is a session secret: it is exported under `withheld`
  with `reason` `secret`, and a session without one adds no entry.
- **PE23** An unknown subject, and a person who owns no row, answers 200 with
  `data` exactly `{}`, `files` `[]` and `withheld` `[]`; no account is made. The
  answers 401 and 403 are the erase route's, and every answer carries
  `Cache-Control: no-store`.
- **PE24** The log line holds the withheld counts and names nobody.

A source address is exported as stored: its authority carries no credential
part; a secret typed into a path or query is exported as typed.

## Who may call

- **PE25** Only an access token of the instance's Keycloak client, with that
  client as its one audience, type `at+jwt`, an `azp` listed in
  `PRESENTATOR_TOKEN_CLIENTS` and the admin group or a subgroup. `webauth`
  checks it. `/api/admin/` takes an access token alone, never the cookie.
- **PE26** On the rest of `/api/` a person's hal access token is the account
  behind it, admitted as a sign-in admits, with no session. A token beside a
  cookie decides, and an invalid one is refused without falling back to the
  cookie.
- **PE27** Sign-out, back-channel logout and a Keycloak block do not end a
  token before `exp` plus leeway.
- **PE28** The gate exists only while `PRESENTATOR_TOKEN_CLIENTS` names a
  client. With the setting empty (the default) neither the gate nor the route
  exists.

## What is logged

- **PE29** One line per call: the operation, the outcome and the counts. It
  names neither the caller nor the person.
- **PE30** The server writes no access log.

## Known gap: builds that outlive an erasure

The erasure removes every run its decks deliver from. Superseded builds and
leftovers go at the end of the next refresh pass ([builds](reference/builds.md)),
so a leftover can outlive an erasure by one pass, or longer while that pass
refuses.
