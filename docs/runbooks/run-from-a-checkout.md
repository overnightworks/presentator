# Runbook: run an instance from a checkout

Symptom: a developer wants an instance on a machine whose decks are all their
own, and the start refuses or the pages do not answer. Impact: no local
instance.

A deck is code, so an instance normally builds every deck in the sealed build
workers ([constraints and reasons](../ARCHITECTURE.md#6-constraints-and-reasons)).
A run from a checkout is the one place that says otherwise:
`PRESENTATOR_BUILD_RUNNER=host` executes a deck's Vue components as the server
process, with its rights over every file and the network. Do not use it for a
repository somebody else can push to.

## Check

Open the public origin, under its hostname: a signed-out visitor sees
`/login`. Any other `Host`, 127.0.0.1 included, gets 404, except the
back-channel logout path, which answers a `POST` (other methods answer 405).
`GET /health` answers 503 while the database or Redis does not answer. A
refused start ends in one `process.failed` line that names the setting and
what is wrong with it, never a secret's value
([configuration](../configuration.md)).

## Fix

1. Supply every setting of [configuration](../configuration.md), in the shell
   or in a gitignored `.env` at the repository root, with
   `PRESENTATOR_BUILD_RUNNER=host`. The public and talk origins are https on
   two hostnames behind a front that terminates it: over plain http a browser
   does not send the `Secure` cookie back, so a local sign-in needs an https
   front. Secrets are files only you can read: the master key (a fresh random
   value of at least 32 characters), the database password and the Keycloak
   client secret. Rollback: remove the `.env` and the files.
2. Set up the Keycloak client as in [configure Keycloak](configure-keycloak.md).
   Rollback: as that runbook says.
3. Reach a Postgres and a Redis from the checkout, such as local containers.
   Redis holds the request budgets and the ask lease, nothing that must
   survive. Rollback: remove the containers.
4. Install the build toolchain on this machine
   ([install it](install-the-host-build-toolchain.md)).
5. In `frontend/`, run `pnpm install --frozen-lockfile` and then
   `pnpm build`; without `frontend/build` the start refuses. Rollback: delete
   `frontend/build`.
6. From the root, run `uv run presentator migrate`, then `uv run presentator`.
   Rollback: stop the process; the database keeps its revision.
7. Re-run the Check.

A checkout has no route to the cluster's speech service or model gateway, so
its speaking and hearing report the service as unreachable and every
co-presenter ask ends in an `error` event.

## Escalate

A start the settings refuse is fixed in the setting the line names. When the
identity provider turns the sign-in down, the Keycloak realm's owner fixes the
client ([configure Keycloak](configure-keycloak.md)). When a deck must build
for others, it does not run here: the platform owner runs the sealed build
workers.
