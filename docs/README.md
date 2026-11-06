# Documentation map

Audience: a human or an agent who has just opened `docs/` and needs to know
which layer answers which question.

This file is a map. It owns no product fact. Each layer has its own owner; where
this map and an owner disagree, the owner is right.

## Which question lives where

| Question | Layer | Owner |
| --- | --- | --- |
| Why does this tool exist, and what does the operator want from it? | Vision | [`VISION.md`](VISION.md) |
| What does the instance do, exactly? | Reference | the documents under [Reference](#reference) |
| How is it built, and what does the design rest on? | Architecture | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| How do I run, change or repair this installation? | Runbooks | the documents under [Runbooks](#runbooks) |

Agent policy lives in [`AGENTS.md`](../AGENTS.md) at the repository root, not
here. The human entry point is [`README.md`](../README.md).

## Runbooks

One task each, in [`runbooks/`](runbooks/).

- Run: [from a checkout](runbooks/run-from-a-checkout.md),
  [in a container](runbooks/start-an-instance-in-a-container.md),
  [install the host build toolchain](runbooks/install-the-host-build-toolchain.md).
- Sign-in: [configure Keycloak](runbooks/configure-keycloak.md).
- Sources: [add a Git source](runbooks/add-a-source.md),
  [renew a secret](runbooks/renew-a-source-secret.md),
  [renew a webhook secret](runbooks/renew-a-webhook-secret.md),
  [call the fetch-now hook](runbooks/call-the-fetch-now-hook.md).
- Decks: [prepare a talk for the build](runbooks/prepare-a-deck-for-the-build.md).
- Speech: [run the speech service on the host](runbooks/run-the-speech-service-on-the-host.md).

## Reference

The deployment's contract is [configuration](configuration.md), the
people route's contract is [people](people.md) and the sources surface's ruled
sentences are [sources](sources.md). In
[`reference/`](reference/): [sign-in and session](reference/sign-in-and-session.md),
[lobby settings and appearance](reference/lobby-settings-and-appearance.md),
[decks](reference/decks.md),
[builds](reference/builds.md),
[co-presenter and voice](reference/co-presenter-and-voice.md).

## What this map does not do

It lists the runbooks and the reference documents because they are many; it
does not list other files, restate a vision sentence, or repeat a decision. It
does not treat a planned layer as present.
