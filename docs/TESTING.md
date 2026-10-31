# Testing

Audience: a human or agent proving a change — which layer's test is the proof,
what the 100% floor requires, and what a review rejects.

The operator's standing testing and coding conventions own the rules. This file
states their consequences for this repository. It does not restate layer
ownership, picture ownership, or how `main` is protected.

## Which layer proves what

A test sits at the layer that owns the behaviour
([ADR 0001](decisions/0001-enforced-layers.md)). A lower layer does not stand
in for a higher one.

| Layer | Proves | How |
| --- | --- | --- |
| `contracts`, `application` | Pure logic, including the run state machine | Direct calls. Fakes at the ports. No key, no browser, no subprocess. |
| `ports` | A protocol | It is a protocol; it is not proven by a port suite. Application tests against a fake of that port prove it. Tests do not invent the port. |
| `adapters` | The real dependency, in a temporary form | A tmp SQLite file; a tmp bare git repository; a `pnpm` the test wrote, on PATH, for the build adapter. A fixture sits next to the test that reads it. |
| `api` | Routes and wire schemas | FastAPI `TestClient` driving the real routes with the real application and fake or tmp adapters. |
| Template | Lobby HTML as the surface of a route | A ruled person-sentence is proven by a delegated agent driving the real interface. A route or fragment is FastAPI `TestClient` on the real route. A direct Jinja unit test is not the proof. |
| `host` | Composition | Once: start the app with a test configuration. |
| Slidev addon | That a deck staged outside the toolchain builds by the addon's name | Vitest building such a deck with `slidev build` from `slidev/`, as `slidev/tests/addon-package.test.ts` does. |
| Surface | A person does X at the lobby | A delegated agent driving the real interface at the widths the dispatch brief names, taken from the picture at [mockups/README.md](mockups/README.md). One flow per ruled sentence. A unit test is not this proof. |

`gitmirror` is proven the same way as an adapter: against a tmp bare repository,
with no presentation code in the loop.

Core tests import no adapter. The first adapter import moves that module to the
integration suite. A remaining exception is one registered module marker, never
a path allowlist.

## The 100% floor

Every line and every branch is reached. The floor is owned by `fail_under` in
`pyproject.toml` and by Vitest coverage thresholds. It is met only by useful
tests, each pinning one observable behaviour.

A hard line — one that talks to a process, a file, a network, or a composition
choice — is reached in one of three ways:

1. A fake at the port, for contract and application tests.
2. A failing or succeeding dependency in a temporary form, for adapter tests.
3. A test configuration for the composition root.

An unreachable line is removed.

## Naming and shape

The test name states the behaviour (`the composition root refuses to run while
it is empty`, not `test_main`). One behaviour per test. A family that differs
only in data is one parametrized test. Builders and fixtures own arrangement.
Tests reuse production contracts; they do not re-implement construction,
serialization, or path logic to calculate the expected result.

Assert outcomes: the return value, the effect on state, or the error raised. No
private fields, no mock call counts, no pinned log strings, unless the
interaction itself is the contract. No sleeps, no timing races, no order
dependence, no process-global state another test can observe.

## Shipped values

The code policy's Value Ownership rule decides which assertion stays; in this
repository each kind of shipped value is proven here:

- `compose.yaml`: `tests/test_compose.py` parses the file and checks a
  relation read from both owners, for example the back-channel alias against
  `PRESENTATOR_PUBLIC_ORIGIN`, and the socket volume options. The kept sealing
  and hardening keys are still substring matches within the service block, and
  the Docker-authority check matches over the whole file. The live properties
  (limits, hardening, socket volume, gateway) are proven by the sealed probe of
  the mandatory CI job "Build worker: sealed step".
- The Dockerfile's build and wire users: the existence check in the CI image
  job reads the passwd entry of `presentator-build` and `presentator-wire` for
  a non-zero UID and a shell ending in `nologin`, without naming a shipped UID;
  the same step's refusal loop names the reserved UIDs, a protective limit.
- The catalog and the deck manifests: tests read the owner (`en.po`,
  `deck.toml`) or use scenario data. The page tests that still repeat catalog
  sentences are frozen and go with their pages. New tests name a word through
  the catalog rather than copying it.

Two traps are real. A port above 1023 does not prove a non-root user. A
substring match stays green on an apex host, a path prefix or a repeated
option.

A slice that removes copied assertions also gets a separate check of every
removed assertion: two reviews approved the loss of the back-channel proof once.

## Per-behaviour cost

Watch the cost per behaviour, not a ratio of test lines to source lines.
Parametrize and fixture rather than copy. A review rejects:

- a test that exists to touch a line
- a test that re-implements production logic to check it
- a unit test standing in for a surface proof

## Local runs and the CI gate

Local runs are targeted — a named module or `-k` on the behaviour, from the
repository root with `uv run --locked pytest`, or a named file from `slidev/`
with `pnpm exec vitest run`; never the whole suite or a Playwright suite unless
the operator asked. CI on the pull request is the full run and the gate. What
`main` requires is [ci-and-branch-protection.md](reference/ci-and-branch-protection.md). What the workflow runs is
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml). The coverage floor is
the configuration above, not a number restated here.

A resource left to the garbage collector fails the run: pytest's configuration
(`[tool.pytest.ini_options]` in `pyproject.toml`) turns a `ResourceWarning`, and
any exception a finalizer swallows, into a failure, which pytest reports as an
unraisable exception naming the leaked object. The failure can land on a later
test than the one that leaked, because the collector runs when it runs.
`PYTHONTRACEMALLOC=25` on a targeted run shows where that object was allocated.

Targeted browser proofs and live probe stacks follow the global contract's
probe-stack rules; the widths a surface proof drives are taken from the picture
at [mockups/README.md](mockups/README.md).
