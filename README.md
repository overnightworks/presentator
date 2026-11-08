# presentator

presentator is a self-hosted web service for giving a Slidev talk from any browser, with an optional AI co-presenter that speaks the slides and answers the room's questions.

## Quick start

- Run it from a checkout: [run-from-a-checkout](docs/runbooks/run-from-a-checkout.md).
- Run it in a container: [start-an-instance-in-a-container](docs/runbooks/start-an-instance-in-a-container.md).

## Documentation

**Using the lobby and giving talks**

- [Lobby](docs/lobby.md), [sign-in](docs/sign-in.md), [co-presenter](docs/co-presenter.md), [people](docs/people.md).

**Writing decks**

- [Decks](docs/decks.md): the deck folder contract and the build.
- [Prepare a deck for the build](docs/runbooks/prepare-a-deck-for-the-build.md).
- [`examples/`](examples/): decks to start from.

**Operating an instance**

- [Configuration](docs/configuration.md) and [sources](docs/sources.md).
- Run: [from a checkout](docs/runbooks/run-from-a-checkout.md),
  [in a container](docs/runbooks/start-an-instance-in-a-container.md),
  [install the host build toolchain](docs/runbooks/install-the-host-build-toolchain.md).
- Sign-in: [configure Keycloak](docs/runbooks/configure-keycloak.md).
- Sources: [add a Git source](docs/runbooks/add-a-source.md),
  [renew a secret](docs/runbooks/renew-a-source-secret.md),
  [renew a webhook secret](docs/runbooks/renew-a-webhook-secret.md),
  [call the fetch-now hook](docs/runbooks/call-the-fetch-now-hook.md).

**Engineers and agents**

- [Vision](docs/VISION.md), [architecture](docs/ARCHITECTURE.md), [AGENTS.md](AGENTS.md).

**Speech**

- [Run the speech service on the host](docs/runbooks/run-the-speech-service-on-the-host.md) and [`speech/README.md`](speech/README.md).

## Contributing

CI on the pull request is the gate; the [workflow](.github/workflows/ci.yml) defines every check. How a change is proven is stated in [Crosscutting Concepts](docs/ARCHITECTURE.md#5-crosscutting-concepts).

## License

MIT, see [LICENSE](LICENSE).
