# Architecture

Audience: an engineer or agent about to change presentator who needs the mental
model first: what the parts are, which way they depend, and what the design
rests on. Exact behaviour lives in the [reference](reference/), the wish in
[VISION.md](VISION.md).

presentator is one web service that mirrors Slidev decks from git remotes,
builds each one sealed in a second service, and serves the built talk on its own
origin, with an AI co-presenter as an overlay in the presenter's browser.

## 1. Context and Scope

```mermaid
flowchart LR
    person([Person in a browser]) --> presentator
    presentator[presentator<br/>web + build worker]
    presentator -- sign-in --> keycloak[Keycloak]
    presentator -- questions --> gateway[Model gateway<br/>via agent-runtime client]
    presentator -- read-only fetch --> git[(git remotes)]
    presentator -- speak / hear --> speech[Speech service]
    hal[hal and edge] -- deploy, legal pages --> presentator
```

## 2. Building Block View

```mermaid
flowchart TD
    host --> api
    host --> adapters
    host --> agent
    api --> application
    adapters --> ports
    agent --> contracts
    application --> ports
    ports --> contracts
    adapters --> gitmirror
    agent --> arc[agent_runtime_client]
    host --> webauth
    api --> webauth
    agent --> webauth
    adapters --> webauth
```

- **Layers.** `host` composes the instance and depends on `api`, `application`,
  `ports`, `contracts` and on the adapters, which fill the ports (`adapters` and
  `agent`). Each depends only downward; an adapter never reaches up into
  `application`, `api` or `host`, and only the host names an adapter. The
  import-linter contracts in `pyproject.toml` enforce this, run by the quality
  gate's `import-contract` rule. A library an adapter owns behind a port is
  forbidden above the adapters by its own import contract in `pyproject.toml`,
  written with its first import; webauth is forbidden only to `application`,
  `ports` and `contracts`, because `api` and `host` import it directly. The web
  framework (FastAPI, Starlette, Pydantic, uvicorn) is not under this rule.
- **`src/gitmirror`** sits beside the layers: it mirrors any remote read-only
  through `git`, does not import `presentator`, and only adapters name it.
- **`presentator.agent`** alone imports `agent_runtime_client`, at the tag
  `pyproject.toml` pins.
- **webauth** owns the sign-in routes, the gate, the origin check, the body caps
  and the cookies; presentator's SQLite stores fill its ports.
- **`frontend/`** is a SvelteKit single-page shell (`adapter-static`, `ssr =
  false`), built in the image; FastAPI serves it and no Node service runs.
- **`slidev/`** owns every theme and addon a deck may name, among them the
  overlay addon `slidev-addon-presentator`.
- **The build worker** (`host/worker.py`) runs every step of a deck's build
  sealed.

## 3. Runtime View

```mermaid
sequenceDiagram
    participant G as git remote
    participant W as web (poll or hook)
    participant M as mirror (files)
    participant B as build worker
    participant T as talk host
    W->>G: fetch (polling or the fetch-now hook, no payload read)
    W->>M: update bare mirror
    W->>B: build deck (sealed)
    B-->>W: built talk and PDF
    T->>T: serve the talk to its owner
```

Polling runs in the host process (`host/polling.py`); the hook is
`api/hooks.py`. See [sources](reference/sources.md),
[builds](reference/builds.md) and [decks](reference/decks.md).

## 4. Deployment View

`compose.yaml` defines two services, `presentator` (web) and `build-worker`,
which hal deploys with `hal apply`. One process serves the lobby and the talk
host and tells them apart by the `Host` header (`served_by_host` in
`host/main.py`); a request for any other host gets a 404. The host keeps
Docker's default seccomp and AppArmor profiles in force and the image's glibc is
2.36 or later, because the controller reaches `pidfd_open` through ctypes. See
[start an instance](runbooks/start-an-instance-in-a-container.md) and
[configuration](reference/configuration.md).

## 5. Crosscutting Concepts

**Persistence.** SQLite holds accounts, sessions, preferences, sources, runs,
decks and key drafts; files hold mirrors and builds. The database holds no file
content: a build's row names its directory and PDF, and a mirror's directory is
derived from its source's address. `builds/` and `mirrors/` are caches the rows
rebuild. A column goes in two releases, the second dropping it in
`create_tables`, gated on `pragma_table_info` (`adapters/schema.py`), so a
restorable older backup still starts.

**Authentication.** Keycloak groups decide who may enter
([sign-in](reference/sign-in-and-session.md)). There is no Redis: rate limits
are single-process (`SingleProcessRateLimitBackend`) and there is no session
cache. Identity writes run in one transaction under a process lock, with the
audit line after COMMIT (`adapters/identity.py`).

**Wire and errors.** JSON lives under `/api/` and every answer is a typed model.
A signed-out path is one exact optional path (`GET /api/session`), never a
prefix or a catch-all. A store failure while resolving a reader is a 500, never
a signed-out answer. Problems follow RFC 9457 (`api/problems.py`), and a problem
type is a `tag:` URI, because an https address breaks when its page moves.
OpenAPI is generated, not served.

**Words and look.** Every lobby word comes from the gettext catalogs through
Babel (`adapters/catalog.py`), and `scripts/check_catalog_purity.py` holds the
pages to it; a language is a file. The language is the instance's and the theme
is the person's. The look comes from appkit, with presentator's teal in the app
slot (`frontend/src/app.css`). The overlay's controls use English literals, and
the room notice follows the deck's language.

**Trust.** A deck author's code, git remotes and people's input are untrusted.
The operator's settings, his local sources folder and hal's token clients are
trusted. A folder under the local-sources mount is read with the operator's
trust: its repositories, their metadata (a `gitdir` file,
`objects/info/alternates`, a symlink) and everyone who can write there. Across
the speech service's private Unix socket go readiness, PCM, transcript and
spoken sentences; browser identity and credentials do not. No operator blessing
narrows this model yet, so an audit keeps the default trust model.

## 6. Constraints and reasons

- A deck is code: its Vue components run in its build. Only the sealed [build
  worker](reference/builds.md) runs it; the web holds no Docker socket, group or
  client, drops every capability, takes no new privilege and has a read-only
  root, so deck code reaches neither the daemon nor the machine's root.
- The talk has its own origin: one cookie across both hosts would put deck code
  beside the credential it must not reach.
- One process writes the database: a second would need a second write lock over
  the one SQLite file, and the identity lock relies on one writer.
- The plain `http` hop to the model gateway is safe only because
  `hal_agent_presentator` has no host address and no published port; whom the
  gateway admits is hal's.
- Back-channel logout comes from inside, not through the trusted proxy, so its
  `X-Forwarded-For` is ignored.
- The fetch-now webhook holds a per-source secret, not a login: a git host has
  no browser ([sources](reference/sources.md)).
- `/api/admin/` takes a Keycloak access token, not a session cookie, so a forged
  browser session cannot erase a person; the path carries the subject, so the
  server keeps no access log ([people](reference/people.md)).
- The refresh token is stored in plain text because Keycloak renews it only with
  the client secret, which stays outside the database; the instance key belongs
  in the same backup.
- A change to what the instance stores or sends to a provider changes
  presentator's section in `legal/` in the same pull request.

## 8. Risks and Technical Debt

- The overlay's palette can clash with a deck's palette.
- A second writing process would break the one-writer guarantee.
- The git child's environment holds the credential and is readable by the same
  user; a Python `str` cannot be wiped.
- A database copy plus the client secret renews a session until the SSO session
  ends; deleting a row revokes nothing, and freed pages, the `-wal` and backups
  keep ended tokens.
- The hosts are same-site, so deck code on the talk host can set `Domain=`
  cookies for the shared domain.
- Each presenter window mounts its own overlay; nothing enforces one speaker.
