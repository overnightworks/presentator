# Runbook: run the speech service on the host

Reader: the operator of the machine that holds the GPU.

A deployed instance has no voice: the socket is not mounted, so speaking and
hearing report "not yet available" ([ADR 0019](../decisions/0019-presentator-is-an-edge-app.md)).
This runbook runs speech beside a checkout run only.

The voice that answers beside a talk is the speech service (`speech/`), a host
process that binds no TCP port and is reached only through `speech.sock`. Who may use it and what crosses the boundary is
[co-presenter and voice](../reference/co-presenter-and-voice.md); the authority
it shares is [trust boundaries](../explanation/trust-boundaries.md).

## Before you start

- `loginctl show-user <user> -p Linger` says `Linger=yes`, so the user unit
  outlives the shell that started it.
- Speech and the checkout run use the same user
  ([run from a checkout](run-from-a-checkout.md)).
- The Chatterbox provider is a separate closed virtual environment under
  `SPEECH_PROVIDER_ROOT/chatterbox`; speech executes its
  `.venv/bin/presentator-chatterbox-worker` directly, never through PATH.

## Steps

Run speech as a `systemctl --user` unit; it is ready when `GET /health` says
both models are:

```sh
SPEECH_PRIVATE_DIRECTORY=/run/presentator-speech PRESENTATOR_RUNTIME_UID=<uid> \
  uv run presentator-speech
```

Speech owns `SPEECH_STATE_DIRECTORY` (by default
`~/.local/state/presentator-speech`) as a mode-0700 directory. Its mode-0600
`selected-voice` file records the downloaded baseline chosen through
Settings · Voice, so keep it with the host state when moving an instance.

## Check

`speech.sock` serves `/health` and `/hear`. On stage, ask one question and see
the answer arrive.
