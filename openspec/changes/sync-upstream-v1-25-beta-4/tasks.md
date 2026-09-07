## 1. Freeze the candidate

- [x] 1.1 Record baseline, approved beta.4 target, fork preservation map and production dependency evidence in context.md; verify the target is peeled commit 15ccd901.
- [x] 1.2 Validate the retargeted artifacts strictly before integrating code; back up current fixes and preserve unrelated untracked work.
- [x] 1.3 Replace the agent-created beta.3 merge with an uncommitted merge of 15ccd901; preserve all integration fixes and verify merge provenance.

## 2. Resolve and verify fork contracts

- [x] 2.1 Resolve account eligibility with the new clock signature and retained quarantine; verify reauth selection and bridge reuse regressions on beta.4.
- [x] 2.2 Compose administrator route recovery with the standalone key route; verify route-recovery and key-dashboard frontend integration tests on beta.4.
- [x] 2.3 Combine API-key context and audit auto-merged custom behavior; verify UTC+7 limits, import/assignment, control/search, buffers/diagnostics, key APIs/installers and HA deployment regressions.
- [x] 2.4 Resolve beta.4 dependencies from frozen lockfiles; verify native source/lock identity against the rebuilt helper and rerun AnyIO/native boundary checks.
- [x] 2.5 Verify accepted-output-free retry on bridge/websocket paths, including lifecycle identity, affinity, settlement and partial failure; verify plaintext-proxy settings/API warnings and the updated cancellation gate.

## 3. Integrated validation

- [x] 3.1 Run backend unit, integration and simulation coverage for the integrated candidate; record commands, counts, failures and any environment-limited checks.
- [x] 3.2 Run frontend lint/type/tests/build and route browser smoke, retaining before/after route evidence for the UI merge.
- [x] 3.3 Run backend lint, types, architecture/timing gates, Rust checks and local migration validation; classify any baseline failures without weakening gates.
- [x] 3.4 Examine old/new durable operation behavior and record mixed-version rollout constraints for abandoned operations.
- [x] 3.5 Sync the two integration deltas, beta.4 retry/proxy-warning deltas and stable context, then run canonical and change OpenSpec strict validation (22 baseline Purpose warnings recorded, not waived).

## 4. Handoff

- [x] 4.1 Write verification.md with exact source/target, checks, limitations and operator HA rollout prerequisites; verify working tree contains only the intended integration plus preserved unrelated work.
- [ ] 4.2 Verify completion against artifacts and archive only this integration if all required checks pass; otherwise leave unfinished tasks visible with evidence.

Production deployment, commit, push and PR publication are separate operator actions and are not checklist items for this local candidate.
