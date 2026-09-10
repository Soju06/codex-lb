# Change: Refactor traffic parity canary orchestration

## Why

The fast canary is operationally correct, but its orchestration, cleanup, and
success-result generation are split between a host-local Bash script and an
inline Python program. Artifact hashing and JSON writes are also duplicated
across the runner and gates. This makes safety-critical cleanup harder to unit
test and lets host configuration accidentally become a second implementation.

## What Changes

- Move fast-suite orchestration, cleanup, validation, privacy scanning, and
  success-result generation into a typed repository-owned Python module.
- Keep the host-local scheduler configuration declarative: it supplies paths
  and invokes one argv without embedding suite logic.
- Centralize JSON loading, atomic output, file digests, and evidence
  attestations used by the canary and aggregate gates.
- Preserve all trigger, fail-closed, evidence-scope, baseline, cleanup, and
  state-update behavior.

## Impact

- Affected spec: `compatibility-tooling`
- Affected code: traffic-analysis artifact helpers, fast-suite orchestration,
  gate output paths, canary config/tests, and the installed host command
- Runtime proxy behavior and public APIs are unchanged.
