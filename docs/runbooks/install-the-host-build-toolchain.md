# Runbook: install the build toolchain on the host

Symptom: an instance with `PRESENTATOR_BUILD_RUNNER=host` (see
[run from a checkout](run-from-a-checkout.md)) builds no deck, or builds it
and cannot export its PDF. Impact: the deck pages say no talk has been built,
or a deck's export fails and the previously delivered talk keeps standing. The
container image carries its own toolchain; skip this there.

## Check

Push a deck change and open its page: the build ends ready. Without the
toolchain every build fails and the deck pages say no talk has been built; the
cause is in the server log. Without the browser a deck builds and its export
fails.

## Fix

1. Confirm `pnpm` is on `PATH` and the Node version is one that
   `engines.node` in `slidev/package.json` allows, and that
   `PRESENTATOR_TOOLCHAIN` names the project carrying Slidev (default
   `slidev`; [configuration](../configuration.md)). Rollback: none, this step
   changes nothing.
2. In `slidev/`, run `pnpm install --frozen-lockfile`. Rollback: delete
   `slidev/node_modules`.
3. As the user the server runs as, run `pnpm exec playwright install chromium`
   in `slidev/`. The browser lands under that user's home directory.
   Rollback: delete the browser directory it created there.
4. Push a deck change again and re-run the Check.

## Escalate

When the build still fails with the toolchain installed, the deck page carries
the toolchain's own words: hand them to the deck's author
([prepare a deck](prepare-a-deck-for-the-build.md)). When the server log shows
the toolchain project cannot be read, the owner of the host's configuration
fixes `PRESENTATOR_TOOLCHAIN`.

Related: [what a deck may build with](../decks.md).
