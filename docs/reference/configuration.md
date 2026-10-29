# Configuration reference

Audience: an operator or engineer looking up a setting. Settings are
environment variables with the prefix `PRESENTATOR_`, read from the process
environment and, in development, from a gitignored `.env` at the repository
root. A refusal at start names the setting and what is wrong with it, never
the value it was given, so a mistyped secret does not land in the startup
output.

## Every run names

| Setting | Meaning |
| --- | --- |
| `PRESENTATOR_SECRET_KEY_FILE` | Absolute path of a file holding the master key, read once at start, minus one trailing newline; at least 32 characters. `webauth` signs the lobby's session cookie with it, signs the talk host's through a derivation of its own, and, through another, encrypts what a source's row holds ([ADR 0013](../decisions/0013-secrets-at-rest-and-credential-delivery.md)). Without a readable key file the process refuses to start. |
| `PRESENTATOR_PUBLIC_ORIGIN` | The one canonical https origin. An `http://` origin refuses the start, because a browser does not send a `Secure` cookie back over plain http. |
| `PRESENTATOR_TALK_ORIGIN` | The talk host's canonical https origin, on another hostname than `PRESENTATOR_PUBLIC_ORIGIN`; talks are served and signed in there ([ADR 0016](../decisions/0016-a-talk-host-signs-in-by-itself.md)). |
| `PRESENTATOR_AGENT_SERVICE` | Where the agent service answers the co-presenter: one canonical `http` or `https` origin, without credentials or a path. Compose sets `http://agent-service:8080`, the name it has on the `hal_agent_presentator` network. |
| `PRESENTATOR_SPEECH_SOCKET` | Absolute private Unix socket path of the speech service. |
| `PRESENTATOR_OIDC_ISSUER` | The https URL of the Keycloak realm, without a trailing slash; exactly the `issuer` of its discovery document. |
| `PRESENTATOR_OIDC_CLIENT_ID` | The Keycloak client id. |
| `PRESENTATOR_OIDC_CLIENT_SECRET_FILE` | Absolute path of a file holding the client secret, read once at start, minus one trailing newline. |

There is no `/setup` and no first admin: accounts arrive with the Keycloak
sign-in ([sign-in and session](sign-in-and-session.md)).

## Defaults

The value in brackets is what a direct run uses; the container image replaces
some of them.

| Setting | Default | Meaning |
| --- | --- | --- |
| `PRESENTATOR_DATABASE` | `presentator.sqlite3` | The SQLite file. |
| `PRESENTATOR_MIRRORS` | `mirrors` | Where the bare mirrors of the deck sources live. |
| `PRESENTATOR_HOST` | `127.0.0.1` | Listen address. |
| `PRESENTATOR_PORT` | `8000` | Listen port. |
| `PRESENTATOR_TRUSTED_PROXIES` | empty | Comma-separated addresses or networks; see below. |
| `PRESENTATOR_TOKEN_CLIENTS` | empty | Comma-separated client ids (`azp`) whose access tokens may call `/api/` as the person and `/api/admin/` as an admin; empty serves no token route. See [people](people.md). |
| `PRESENTATOR_SOURCE_POLL_SECONDS` | `300` | Interval of the source poll. |
| `PRESENTATOR_SOURCE_TIMEOUT_SECONDS` | `20` | Bound of one pull. |
| `PRESENTATOR_TOOLCHAIN` | `slidev` | The project carrying Slidev. |
| `PRESENTATOR_BUILDS` | `builds` | Where built talks and PDFs are kept. |
| `PRESENTATOR_BUILD_TIMEOUT_SECONDS` | `300` | Bound of each build step; compose also hands it to the build worker by name. |
| `PRESENTATOR_BUILD_OUTPUT_MEGABYTES` | `300` | Size bound of the talk a build leaves; compose also hands it to the build worker by name. |
| `PRESENTATOR_BUILD_RUNNER` | `worker` | Builds through the build worker's socket and refuses to start unless the worker answers within 70 seconds; `compose.yaml` sets it. `host` builds on this machine instead, for a development run ([trust boundaries](../explanation/trust-boundaries.md)). |
| `PRESENTATOR_BUILD_WORKER_SOCKET` | `/run/presentator-build/worker.sock` | Where the web reaches the build worker; the default is where the worker binds, so set it only where the socket volume is mounted elsewhere. |

The container runner's settings `PRESENTATOR_BUILD_IMAGE`, `_VOLUME`, `_MEMORY`
and `_DISK` are gone: a compose instance ignores them in its environment, and a
direct run whose `.env` still names one refuses to start and names the key.

`PRESENTATOR_TOKEN_CLIENTS` replaces `PRESENTATOR_ADMIN_TOKEN_CLIENTS`, with no
alias. To upgrade, set the new key to the old key's value in whichever source
sets it (the process environment, compose or `.env`), remove the old key, and
restart. A `.env` that names the old key with a value refuses to start and
names it; an empty entry is skipped, and in the process environment the old key
is ignored, so a missed rename leaves token access closed. To roll back,
restore the old key and remove the new one, together with the old code: the old
code ignores the new key in the process environment but refuses to start on it
in `.env`.

`PRESENTATOR_SOURCE_URL`, `_REF`, `_NAME`, `_CREDENTIAL` and `_HOOK_SECRET` are
not settings; leaving them in the environment does nothing, and a line naming
`PRESENTATOR_SOURCE_CREDENTIAL` does nothing in `.env` or in the shell.

## Trusted proxies and the client address

The client address is the ASGI peer. Empty `PRESENTATOR_TRUSTED_PROXIES` is
correct for a direct run. Behind a proxy every request shares the proxy's address.
Setting the list to that peer makes the library read `X-Forwarded-For` and
`X-Forwarded-Proto`: the per-address rate limit counts the real client instead
of the proxy, and the fetch-now hook address the Sources page shows names the
https the proxy saw, not the plain http the app itself is served over. A
deployed container has a network of its own and sees the edge's proxy
([ADR 0019](../decisions/0019-presentator-is-an-edge-app.md)) as its gateway
(`172.31.251.1`, a subnet `compose.yaml` fixes). The guard against another site's form does not depend on this list
([sign-in and session](sign-in-and-session.md)).

## In the container image

The first six rows are a fact of the image's filesystem and network, so the
image sets them and the defaults above do not apply; compose sets the rest.

| Setting | In this image |
| --- | --- |
| `PRESENTATOR_DATABASE` | `/data/database/presentator.sqlite3` |
| `PRESENTATOR_MIRRORS` | `/data/mirrors` |
| `PRESENTATOR_BUILDS` | `/data/builds` |
| `PRESENTATOR_LOCAL_SOURCES_MOUNT` | `/data/local-sources` |
| `PRESENTATOR_TOOLCHAIN` | `/app/slidev` |
| `PRESENTATOR_HOST` | `0.0.0.0`, reached through the edge's proxy |
| `PRESENTATOR_BUILD_RUNNER` | `worker`, set by `compose.yaml` |
| `PRESENTATOR_AGENT_SERVICE` | `http://agent-service:8080`, set by `compose.yaml` |
| `PRESENTATOR_SPEECH_SOCKET` | `/run/presentator-speech/speech.sock`, not mounted in a deployed instance |

An instance configuration that sets `PRESENTATOR_TOOLCHAIN` to `/app/frontend` by hand must drop it: the image sets it to `/app/slidev`.

`Dockerfile` builds one image in two layers: the Slidev toolchain with the
Chromium its PDF export drives (what a deck's own build runs in), and the whole
instance on top of it. The image holds no Docker client. It carries the CA
certificates an HTTPS source needs to verify the git host's TLS certificate;
CI proves it with a live HTTPS fetch on every build.

The compose file needs no value from the operator: its plain values are
literals, and `COMPOSE_PROJECT_NAME` is only a probe's.

## Speech (host process)

`SPEECH_*` settings of the host process are named in
[run the speech service](../runbooks/run-the-speech-service-on-the-host.md).
