# Runbook: unpin a Git host's key

Symptom: an SSH source reads Refused with the reason *Host key refused*, at
*Check connection* or on the source's page. Impact: every source on
that host stops fetching, and its decks get no updates, until the host's pinned
key is removed ([SR12](../sources.md)).

## Check

On the source's page choose *Fetch now*: the source's state reads reachable.

## Fix

1. Ask the Git host's owner whether the host's key really changed, and compare
   the new key's fingerprint with the one the host publishes. Never unpin a key
   you have not verified: a changed key is also what a machine in the middle
   answers with. Rollback: none, this step changes nothing.
2. Run `presentator unpin-host <host>` with the instance's two database
   settings ([configuration](../configuration.md)); `<host>` is the name the
   address names, or `[host]:port` for another port than 22. It exits 0 once
   the host's pin is gone, and 3 when nothing was pinned for that name. It
   exits 1 with one `process.failed` line when the database fails it; its
   `reason` names why ([configuration](../configuration.md)), and
   `statement_cancelled` means another session held the pins past
   `PRESENTATOR_DATABASE_STATEMENT_TIMEOUT_SECONDS`: run it again.
   Rollback: none needed; the next fetch pins the key the host answers with.
3. Choose *Fetch now*. The first contact pins the host's new key for every
   process. Rollback: unpin again and fix whatever the host answered.

## Escalate

When the host's owner cannot confirm the change, leave the pin in place and
treat the host as compromised.
