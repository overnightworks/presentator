# Runbook: run the live journey of the lobby

Symptom: a rollout or release must be shown to keep the lobby's pages and
session whole on the live system. Impact: without the run, a page load that
loses its files or a session that ends mid-rollout goes unseen.

The journey signs in as the test person with the cookies of a storage state and
loops the lobby flow for a span: it opens the lobby page and loads every file
the page requests, reads `GET /api/session`, which must be signed in, and reads
`GET /api/decks`, where an empty list passes. A round holds when every answer
is a success and the session survives. The journey is a `live`-marked test under
`tests/e2e/live`; CI collects it and never runs it.

## Check

The task `live:journeys` ends with exit 0 when every round held, 1 when a round
failed, and a non-zero exit with no round run when `--storage-state` names no
readable file. Its output is codes and counts only, such as
`rounds=32 failed=0`, or the failing steps as `step=status` counts. The JUnit
report lands in the git-ignored `reports/`.

## Fix

1. Prepare the checkout: run `uv sync --locked` at the root, then
   `uv run --no-sync playwright install chromium`. The task changes nothing in
   the checkout and installs no browser. Rollback: delete `.venv` and the
   browser under `~/.cache/ms-playwright`.
2. Run the journey through platform's live-check command, which signs the test
   person in, passes the cookies as the storage state and deletes the file on
   every exit: `mise run live:check presentator --tier journeys --checkout <dir>`
   from the platform checkout. Rollback: none, the run only reads.
3. To run the task by hand against an origin, with a storage state of your own
   that only you can read: `mise run live:journeys --origin <origin>
   --storage-state <file>`, with `--loop-span <seconds>` to change the span
   (default 120, and well inside platform's cap of 1830). Delete the file after.

## Escalate

A failed round names its step and status: `page`, `file`, `session` or `decks`
with the status, 0 for no answer, or `session=signed-out`. A `file` or `page`
failure during a rollout is a pod that served a page without the files of its
build; the owner of the deployment is platform. A `session=signed-out` with a
fresh state is sign-in's, see [sign in](../sign-in.md).
