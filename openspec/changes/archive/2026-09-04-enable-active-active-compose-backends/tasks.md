## 1. Active-active topology

- [x] 1.1 Add the private surge service, make blue and green steady-state HAProxy members, and verify the rendered Compose and HAProxy configuration.

## 2. Capacity-preserving rollout

- [x] 2.1 Implement legacy-state compatibility and runtime surge registration, with automated coverage for topology migration.
- [x] 2.2 Implement the readiness-gated surge rollout, fail-closed recovery behavior, rollback of the current base-slot drain, and topology-aware status output, with regression tests.

## 3. Operator contract

- [x] 3.1 Update the deployment specification, context, and Docker deployment guide, then validate the OpenSpec change strictly.
- [x] 3.2 Update the repository HA deploy skill and its agent metadata for the active-active surge workflow, then validate the skill structure.

## 4. Verification

- [x] 4.1 Run the focused test suite, shell/configuration validation, and strict OpenSpec validation; record any environment-limited checks.

Verification notes:

- `shellcheck` is unavailable on this host; `bash -n` passed for the deployment script.
- Focused pytest and Ruff checks passed, Compose rendered successfully, HAProxy 3.2 accepted both the static configuration and the runtime dynamic-server/address-update commands, and the deploy skill validator passed.
- Strict validation passed for this change and for `deployment-installation`. Repository-wide strict spec validation remains red on pre-existing placeholder-purpose warnings in unrelated capabilities.
