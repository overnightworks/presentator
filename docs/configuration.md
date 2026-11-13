# Configuration

Audience: the operator whose deployment supplies the settings, and an
engineer looking one up. Settings are environment variables with the prefix
`PRESENTATOR_`, read from the process environment and, in development, from a
gitignored `.env` at the repository root. A refusal at start names the
setting and what is wrong with it, never the value it was given, so a mistyped
secret does not land in the startup output.

## Every run names

| Setting | Meaning |
| --- | --- |
| `PRESENTATOR_SECRET_KEY_FILE` | Absolute path of a file holding the master key, read once at start, minus one trailing newline; at least 32 characters. `webauth` signs the lobby's session cookie with it, signs the talk host's through a derivation of its own, and, through another, encrypts what a source's row holds. Without a readable key file the process refuses to start. Keep it in the deployment's secret store, apart from the database backup: an instance restored without it lists its decks and can pull none of them, and a source's secret is [renewed](runbooks/renew-a-source-secret.md) if it is unreadable. |
| `PRESENTATOR_PUBLIC_ORIGIN` | The one canonical https origin. An `http://` origin refuses the start, because a browser does not send a `Secure` cookie back over plain http. |
| `PRESENTATOR_TALK_ORIGIN` | The talk host's canonical https origin, on another hostname than `PRESENTATOR_PUBLIC_ORIGIN`; talks are served and signed in there. |
| `PRESENTATOR_AGENT__BACKEND` | Which backend answers the co-presenter: `litellm`, the one value; any other, `service` included, refuses the start. No default. |
| `PRESENTATOR_AGENT__GATEWAY__ORIGIN` | Where the LiteLLM gateway answers: one `http` or `https` origin, scheme, host and optional port, without a path, credentials, query or fragment. No default. |
| `PRESENTATOR_AGENT__GATEWAY__KEY_FILE` | Absolute path of the file holding this app's gateway key. It is read for every question, not at start, so a missing file refuses the question and not the start. The file holds the key alone on one line of printable ASCII without a space, 1024 bytes at most; the client strips one trailing `\n`, so any other control character, a `\r` from a Windows editor included, refuses every question with only `key_unusable` logged. No default. |
| `PRESENTATOR_AGENT__API_GROUP` | The Keycloak group path whose members the co-presenter answers; any other person is answered "not enabled". No default. |
| `PRESENTATOR_AGENT__GATEWAY__CA_FILE` | Optional absolute path of the CA file an `https` gateway origin needs; an `http` origin forbids it. |
| `PRESENTATOR_SPEECH_SERVICE` | The one plain `http` origin of the shared speech service, without a path. No default. |
| `PRESENTATOR_SPEECH_SERVICE_SCOPE` | The one scope token that names the service at the issuer; both sign-ins and the workload grant ask for it. No default. |
| `PRESENTATOR_SPEECH_VOICE_DE` | The voice that speaks a German answer, a voice id from the speech service's catalogue, the speech client's `VoiceId`, that speaks German; any other value refuses the start. Default `de_DE-thorsten-medium`. |
| `PRESENTATOR_SPEECH_VOICE_EN` | The voice that speaks an English answer, a catalogue voice id that speaks English; any other value refuses the start. Default `en_US-norman-medium`. The co-presenter is ready to speak only when the service offers both voices as ready; a voice of several languages, such as `ryan`, may stand in either setting. A `.env` that still names the removed `PRESENTATOR_SPEECH_VOICE` refuses the start, because `.env` forbids unknown entries. |
| `PRESENTATOR_OIDC_ISSUER` | The https URL of the Keycloak realm, without a trailing slash; exactly the `issuer` of its discovery document. |
| `PRESENTATOR_OIDC_CLIENT_ID` | The Keycloak client id. |
| `PRESENTATOR_OIDC_CLIENT_SECRET_FILE` | Absolute path of a file holding the client secret, read once at start, minus one trailing newline. |

There is no `/setup` and no first admin ([sign-in](sign-in.md), SI2).

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
| `PRESENTATOR_TOKEN_CLIENTS` | empty | Comma-separated client ids (`azp`) whose access tokens may call `/api/` as the person and `/api/admin/` as an admin; empty serves no route for a token client's token. See [people](people.md). |
| `PRESENTATOR_SOURCE_POLL_SECONDS` | `300` | Interval of the source poll. |
| `PRESENTATOR_SOURCE_TIMEOUT_SECONDS` | `180` | Bound of one pull and of one Check connection. |
| `PRESENTATOR_TOOLCHAIN` | `slidev` | The project carrying Slidev. Read once at start under either runner; a project that cannot be read refuses to start. |
| `PRESENTATOR_LOBBY_BUILD` | `frontend/build` | The lobby app's `pnpm build` output; an instance without a build it can serve refuses to start. |
| `PRESENTATOR_BUILDS` | `builds` | Where built talks and PDFs are kept. |
| `PRESENTATOR_BUILD_TIMEOUT_SECONDS` | `300` | Bound of each build step; the build worker reads it too. |
| `PRESENTATOR_BUILD_OUTPUT_MEGABYTES` | `300` | Size bound of the talk a build leaves; the build worker reads it too. |
| `PRESENTATOR_BUILD_RUNNER` | `worker` | Builds through the build worker's socket and refuses to start unless the worker answers within 70 seconds. `host` builds on this machine instead, for a development run (without the sandbox, whose reason is in [constraints and reasons](ARCHITECTURE.md#6-constraints-and-reasons)). |
| `PRESENTATOR_BUILD_WORKER_SOCKET` | `/run/presentator-build/worker.sock` | Where the web reaches the build worker; the default is where the worker binds, so set it only where the socket volume is mounted elsewhere. |

The build worker tolerates `PRESENTATOR_WORKER_SEAL` in its environment and reads it nowhere; the platform's manifest still sets it.

## Trusted proxies and the client address

The client address is the ASGI peer. Empty `PRESENTATOR_TRUSTED_PROXIES` is
correct for a direct run. Behind a proxy every request shares the proxy's address.
Setting the list to that peer makes the library read `X-Forwarded-For` and
`X-Forwarded-Proto`: the per-address rate limit counts the real client instead
of the proxy, and the fetch-now hook address the Sources page shows names the
https the proxy saw, not the plain http the app itself is served over.
The guard against another site's form does not depend on this list
(webauth's [host contract](https://github.com/overnightworks/webauth/blob/main/docs/host-contract.md)).

## In the container image

These rows are a fact of the image's filesystem and network, so the image sets
them and the defaults above do not apply.

| Setting | In this image |
| --- | --- |
| `PRESENTATOR_DATABASE` | `/data/database/presentator.sqlite3` |
| `PRESENTATOR_MIRRORS` | `/data/mirrors` |
| `PRESENTATOR_BUILDS` | `/data/builds` |
| `PRESENTATOR_LOCAL_SOURCES_MOUNT` | `/data/local-sources` |
| `PRESENTATOR_TOOLCHAIN` | `/app/slidev` |
| `PRESENTATOR_LOBBY_BUILD` | `/app/lobby` |
| `PRESENTATOR_HOST` | `0.0.0.0`, reached through a proxy |

## Ruled sentences

This section holds the configuration rulings, each with the ID its test cites.

- **CF2.** The talk origin is configuration, on its own canonical https
  hostname. Proven by `test_the_talk_origin_must_be_another_canonical_https_hostname`
  in `tests/presentator/host/test_oidc.py`.
- **CF3.** The talk-session key is derived from the master key under its own
  purpose; there is no second secret. Proven by
  `test_the_talk_session_secret_is_long_and_shares_no_bytes_with_another_purpose`
  in `tests/presentator/adapters/test_secrets.py`.
- **CF5.** The co-presenter's backend takes exactly `litellm`, with no default.
  Proven by the tests of `tests/presentator/host/test_config_agent.py` whose
  docstring cites CF5.
