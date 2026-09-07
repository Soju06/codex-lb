## Context

See [proposal.md](proposal.md) for scope and [context.md](context.md) for baseline evidence. The original worktree had no tracked edits; `simplify-install-one-liners/` is unrelated untracked work. The current beta.3 merge and integration fixes must be backed up before retargeting. Git predicts four conflicts against beta.4, including websocket imports. The operator approved the new pinned target.

## Goals / Non-Goals

Produce a locally verified integration of the pinned release and preserve the 19 fork commits. Do not change public contracts beyond the imported release and route-recovery composition. Do not create commits, push, publish a PR, deploy, or modify production state as part of candidate preparation.

## Decisions

1. Integrate the peeled beta.4 commit with a non-committing merge on the current branch. Back up the beta.3 worktree and its integration fixes, abort only that agent-created merge, then re-merge beta.4 and reapply the fixes. Preserve Git's merge provenance for a later explicitly requested commit. A whole release keeps related cleanup, clock, retry, dependency, and test changes together; selective cherry-picks would require reconstructing their dependencies. The source and target commit IDs remain fixed even if main advances.
2. In `account_eligibility.py`, retain status-based quarantine independent of token expiry while accepting upstream's caller-supplied `now` argument. Review every call and retain the all-reauth error behavior. Do not take the upstream function body wholesale.
3. In `App.tsx`, keep `/key-dashboard` outside `AuthGate`; place upstream's error/loading boundaries and wildcard not-found route in `AdministratorApp`/`AppLayout`. Preserve route-level lazy loading. Combine both API-key context sections, including UTC+7 scheduling and reservation estimation.
4. Audit automatically merged code around reauth selection/reuse, native websocket failure metadata, compression/control content types, and API-key settlement. Keep fork-owned HA scripts, limits, installers and import behavior. Validate overlapping expectations through tests, not conflict count.
5. Keep Python and frontend dependency resolution frozen to imported lockfiles. Rebuild the Rust helper because compression support changes there. Run tests with synthetic local data; never point test fixtures at production PostgreSQL or credential files.
6. Sync this change's two delta contracts and the beta.4 retry/proxy-warning deltas into canonical specs after checking their implementation and scenario coverage. Preserve imported upstream changes as their existing owners; do not mass-archive upstream or unrelated fork work.
7. Preserve beta.4's bounded accepted-output-free retry without weakening file/account affinity, reservation settlement, cancellation cleanup or the single visible response lifecycle. Keep transport failure metadata in shared support code to avoid bridge/websocket import cycles. Verify plaintext-proxy warnings without changing production endpoints or reading credentials.

## Risks / Trade-offs

- Beta software and large proxy delta: run targeted regressions first, then backend unit/integration/simulation, frontend, Rust, lint/type/architecture and strict OpenSpec checks as applicable. Upstream green CI is supporting evidence, not proof for the merged fork.
- Persisted `abandoned` operations: no Alembic revision changes, but old/new replicas share data during a surge rollout. Inspect old readers/writers and record any overlap or rollback limitation before approving a production candidate.
- Private behavior drift: validate quarantine, UTC+7 boundary, multi-file import, proxy assignment, native buffer ceilings/diagnostics, standalone route/install, and search/control-response behavior explicitly.
- Architecture/type checks may reveal pre-existing fork violations: attribute them to baseline or integration and record exact scope; do not raise ratchets or suppress checks.

## Migration Plan

1. Record baseline and verify the frozen candidate locally. Build/test the native helper and frontend before endpoint smoke checks.
2. Write a verification report with pass/fail results and unresolved production prerequisites. Archive this integration only after its required verification passes; do not mark failed or omitted checks as passed.
3. For a later explicitly requested production deploy, use the `codex-lb-ha-deploy` skill. Check HA status, database connection and memory headroom, and mixed-version compatibility first. Use only `./scripts/deploy-compose-ha.sh deploy`.
4. Confirm three healthy eligible backends (`blue,green,amber`), phase `none`, surge stopped/ineligible, public readiness, and AnyIO >=4.14 on each runtime. Observe cancellation/cleanup errors and failed requests through the rollout.
5. On failure, inspect the script-owned phase and serving slots. Rollback is not a general version revert: only an explicitly requested cancellation of a visible healthy drain is supported. Do not reset runtime state manually or promise uninterrupted long-lived connections past the drain deadline.
