# presentator

## Commands

- `uv run --locked pytest <module or -k expression>` from the root: runs the Python tests; run one module, CI runs the rest.
- `pnpm exec vitest run <file>` from `slidev/` or `frontend/`: runs one front-end test file; needs `pnpm install --frozen-lockfile` there first.
- `uv run --locked ruff check` and `uv run --locked ruff format --check` from the root: lint and format.
- `uv run --locked pyright` and `uv run --locked vulture` from the root: types and dead code.
- `uv run --locked python scripts/check_catalog_purity.py` from the root: finds literal page text.
- `pnpm lint` and `pnpm check` from `frontend/`: lint and types of the lobby pages.
- `pnpm lint` and `pnpm typecheck` from `slidev/`: lint and types of the deck build.
- `uv run --locked python -m tests.support.lobby_drive openapi frontend/src/lib/api/openapi.json` from the root, then `pnpm generate:types` from `frontend/`: regenerates the OpenAPI document and the wire types.

## Rules

- A new mandatory CI job is added to `quality/config.toml` by its display name.
- `sonar-project.properties` carries only the keys the quality gate allows.
- A new root entry is declared in `quality/config.toml` with a reason, or lives in its owner's directory.
- A change to the lobby API or to `LobbyText` regenerates `openapi.json` and `types.ts`; CI diffs them.
- Page text comes from `LobbyText` and `src/presentator/adapters/catalogs/en.po`, never a literal in a `.svelte` file.
- A `LobbyText` field only a page reads is listed in `tests/support/vulture_allowlist.py`.
- `legal/` changes only by wording the operator released.

## Owners

- The operator decides product, legal wording and releases.
- speech-runtime decides the speech service and its client.
- agent-runtime decides the agent client and gateway.
- webauth decides sign-in.
- quality decides the gate.
- platform decides deployment and the shared privacy sections.

## Documents

- `README.md`: read first, it routes each reader to a document.
- `docs/VISION.md`: read when you judge whether a change fits the product.
- `docs/ARCHITECTURE.md`: read when a change moves a part, a boundary or a guard.
- `docs/co-presenter.md`, `docs/configuration.md`, `docs/decks.md`, `docs/lobby.md`, `docs/people.md`, `docs/sign-in.md`, `docs/sources.md`: read the one whose area you change; each holds that area's ruled behaviour.
- `docs/runbooks/`: read when you run, configure or renew something by hand.
- `legal/`: read when a change alters what is processed.
