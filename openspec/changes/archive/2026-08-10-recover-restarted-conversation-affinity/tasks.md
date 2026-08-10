## 1. Regression Coverage

- [x] 1.1 Add a routed `/backend-api/codex/responses` regression proving a marked self-contained restart escapes an unavailable legacy owner and subsequent continuity stays on the replacement.
- [x] 1.2 Add negative coverage for ordinary requests, account-dependent payloads, healthy owners, and compare-and-set races.
- [x] 1.3 Add routed regressions for scoped-owner retirement and synthesized WebSocket turn-state cleanup.
- [x] 1.4 Add regressions for canonical compatibility classification, stale post-retirement selection inputs, and live HTTP bridge bypass.
- [x] 1.5 Add regressions proving a colliding explicit turn state retains its owner and model eligibility does not narrow mutation authority.
- [x] 1.6 Add regressions proving old-reader fail-closed behavior and CAS-loser exclusion of a concurrently retired owner.

## 2. Implementation

- [x] 2.1 Expose goal-continuation marker detection and derive the typed restart capability only for account-neutral fresh-replay payloads.
- [x] 2.2 Add atomic unavailable-owner tombstoning guarded by mapping owner and account status.
- [x] 2.3 Thread the capability through HTTP and WebSocket selection and rerun normal selection after successful retirement.
- [x] 2.4 Restrict retirement mutation to the authenticated effective account scope and preserve generated turn-state cleanup across account changes.
- [x] 2.5 Prevent bridge reuse/forwarding and stale account snapshots from bypassing or undoing successful restart retirement.
- [x] 2.6 Persist source-qualified session-header abandonment while retaining hard explicit turn-state ownership.
- [x] 2.7 Separate authenticated sticky-mutation authority from model and service-tier replacement eligibility.
- [x] 2.8 Encode source-qualified retirement so legacy readers retain hard ownership, and preserve the retired owner as exclusion evidence on every typed read.

## 3. Validation and Documentation

- [x] 3.1 Run focused regressions, relevant sticky/session suites, lint/type checks, and strict OpenSpec validation.
- [x] 3.2 Review the diff for fail-closed continuity, async/session ownership, transport parity, and simplicity-gate compliance.
- [x] 3.3 Promote stable context to the main capability docs and verify the change.
- [x] 3.4 Add reversible migration coverage for source-qualified sticky abandonment metadata.
- [x] 3.5 Document and validate rolling-version marker semantics plus concurrent retirement handling.

## 4. Local Deployment

- [x] 4.1 Build a revision-labelled local Docker image without exposing deployment secrets.
- [x] 4.2 Replace the running codex-lb container with rollback protection and verify health plus deployed revision.
