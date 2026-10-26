Audience: every agent that edits this repository.

Code policy and coordination live once, in the global contract. This file adds
only this repository's facts.

Documentation starts at `docs/README.md`, which maps each layer to its owner.

## Checks

CI calls the shared quality gate from the `quality` job of
`.github/workflows/ci.yml`, pinned by SHA. Its required check is
`quality / verdict`; `quality/config.toml` lists the jobs that must be green
for it, so a new mandatory CI job is added there by its display name. Secret
scanning and the SonarCloud scan run inside the gate, and
`sonar-project.properties` may carry only the keys the gate allows.

The gate's root-layout rule holds the root entries: a new root entry is declared
in `quality/config.toml` with a reason, or lives in its owner's directory.
