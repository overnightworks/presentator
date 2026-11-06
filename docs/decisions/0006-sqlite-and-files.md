# ADR 0006: SQLite holds the records and the filesystem holds the files

Audience: humans and agents storing anything that must survive a restart.

- Status: ACCEPTED 2026-09-06 — the stores are phase M0 of
  [VISION.md](../VISION.md); amended 2026-11-06 (no in-place column adding or failure-reason reconciliation)
- Date: 2026-09-06
- Decision authority: the operator's ruling of 2026-09-06 recorded on
  [#2](https://github.com/overnightworks/presentator/issues/2)
- Neighbours: [ADR 0005](0005-deck-folder-and-slidev.md) owns what a deck is;
  [ADR 0003](0003-libraries-for-models-and-auth.md) requires a session store
  without Redis

## Context

This installation is one home server with one operator. The durable facts are
few and small: who may log in, which sessions are open, which runs happened, and
what the audience asked and got answered. The bulky things — decks, Slidev
builds, cached audio — are files that are already files.

Songmaker's infrastructure is the nearby tempting example, and it is the wrong
one: it needs Postgres, Redis, a job queue, and monitoring because it runs long
generation jobs for many users. This product has no long job. A presentation run
is interactive, is over when the talk is over, and has exactly one person
waiting on it.

## Decision

SQLite owns users, sessions, runs, and the question-and-answer log. The
filesystem owns deck folders, Slidev builds, and the audio cache.

A file's path is a record's field, not the other way round: the database never
holds a blob, and the filesystem never holds a fact the database is supposed to
answer.

Both live behind ports ([ADR 0001](0001-enforced-layers.md)), so the store is
replaceable if this decision's assumption ever stops holding.

## Consequences

- Backup is a file copy, and inspection is one `sqlite3` command.
- There is no separate service to start, upgrade, or monitor beside the app.
- A column the code no longer needs goes in two releases. The first stops reading it, still writes what an earlier release needs to read it (a `NOT NULL` column forces a placeholder on the first insert), and on every start folds what must be folded (a person row that held no theme says `follow-system`). The second drops it in place through `create_tables` (`adapters/schema.py`), gated on `pragma_table_info`, in one transaction and losing no row, and only once a release carrying the first step has been live: hal deploys only the newest tag, so a drop tagged at once leaves the first release never run. So the way back from either release is a newer tag that reverts it, and that code reads the file as it is. One gap stays: after the drop, the first release's code, brought back by a revert, fails every insert that still names the dropped column, so a tag that reverts the drop keeps the drop's own schema and insert. The backup taken before the start is the backstop for damaged data, never the way back from a release. Source: the head's proposal on [#785](https://github.com/overnightworks/presentator/issues/785), 2026-11-05, not the operator's wording. `person_preferences.language_tag` went in one step, before this rule, so the release before F4a cannot read the migrated file.
- A column an operated file lacks is added in place by `create_tables`
  (`adapters/schema.py`), additive and in one transaction, losing no row; every
  statement names its columns, so the release before still reads the file. On
  every start, in the same transaction, `create_tables` also makes the failure
  reason agree again with what that release wrote beside it, so going forward
  after a rollback reads every row. Two gaps stay: a rolled-back release that
  fails without words over a row still carrying a server reason leaves that
  reason, and an upgraded failed row whose text was one of the server's sentences
  reads as `toolchain`; the page shows the sentence of either until the deck's next build.
- SQLite's single writer is enough for one operator and one run; concurrent
  talks by different users would not be, and that is the trigger to revisit.
- One process writes the database, and the identity stores' write lock depends on
  that ([ADR 0003](0003-libraries-for-models-and-auth.md)). A second writer is
  the trigger to give them a unit of work.
- The audio cache grows on disk with nothing evicting it until something is
  written to do so.
- This record does real work when other decisions are taken. In the
  build-vs-reuse survey of 2026-09-06 it is the reason three otherwise
  reasonable candidates were rejected: LiveKit's media server
  ([ADR 0002](0002-server-owned-run.md)), fastapi-admin's Redis requirement
  ([ADR 0008](0008-lobby-server-rendered.md)), and a TURN server for WebRTC
  ([ADR 0007](0007-browser-client-behind-tunnel.md)).

### Amendment 2026-11-06 — the store no longer adds columns or reconciles failure reasons

`create_tables` no longer adds columns in place or reconciles failure reasons, because every operated file is born with them ([#508](https://github.com/overnightworks/presentator/issues/508)). The Consequences bullet on adding a column in place records the earlier state; the dropping of the instance theme column stays until [#823](https://github.com/overnightworks/presentator/issues/823), so the bullet on two-release column removal still holds. Authority: [#819](https://github.com/overnightworks/presentator/issues/819), the fresh-volume ruling on #508 and the operator's ruling on #818.

## Rejected alternatives

- **Postgres.** A second service and a second thing to operate, bought for
  concurrency that one operator does not generate.
- **Redis.** Nothing here needs a shared in-memory store, and requiring one
  would make the app un-runnable on a laptop. This rule survives contact with
  `webauth`, whose session cache and rate limiter ship Redis implementations:
  this product supplies SQLite ones behind the library's ports instead, and
  [ADR 0003](0003-libraries-for-models-and-auth.md) owns which ports those are.
- **A job queue.** A queue exists to defer work past the caller's patience. Here
  the caller is the operator on stage: nothing may be deferred, and a run that
  is not immediate has already failed.
- **A monitoring stack.** One machine, one user, and logs he can read.
