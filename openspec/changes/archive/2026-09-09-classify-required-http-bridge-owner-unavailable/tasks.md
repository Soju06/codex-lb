## 1. Selection And Health

- [x] 1.1 Carry explicit request-ownership and continuity-owner provenance into account selection.
- [x] 1.2 Return typed owner-unavailable and owner-policy-conflict results after supported reload paths.
- [x] 1.3 Preserve local-cap, authorization, security, model, quota-policy, and configured single-account behavior.
- [x] 1.4 Suppress global degraded transitions only for explicit ownership-restricted and resolved hard-sticky selections.

## 2. Verified Replay

- [x] 2.1 Centralize a fail-closed account-neutral, self-contained Responses replay predicate.
- [x] 2.2 Require durable input count/fingerprint prefix proof and a typed pre-visible owner miss.
- [x] 2.3 Remove previous-response and stale affinity state, exclude the failed owner, and create a local namespaced recovery lane.
- [x] 2.4 Preserve fail-closed behavior for unsafe state, policy conflicts, post-selection failures, and partial output.
- [x] 2.5 Require completed retained response output and ordered fresh input after the durable prefix proof.
- [x] 2.6 Project verified full resends to portable plaintext by removing reasoning, upstream item identities, and completed search bookkeeping while validating retained client turn metadata.

## 3. Durable Recovery Ownership

- [x] 3.1 Add narrow task-specific alias precedence for namespaced recovery lanes.
- [x] 3.2 Fence alias replacement atomically and distinguish owner fencing from protected-alias rejection.
- [x] 3.3 Preserve recovery ownership through forwarding, reconnect, prewarm, authorization, and model transitions.
- [x] 3.4 Retain recent restart proof without extending its age and purge stale proof with existing cleanup.
- [x] 3.5 Compensate pre-dispatch alias publication on cancellation and retire ambiguous visible/prewarm sends.

## 4. Maintainability And Coverage

- [x] 4.1 Extract selection inputs, unpinned selection, budget policy, and bridge session-registry responsibilities without changing compatibility surfaces.
- [x] 4.2 Add unit coverage for safety classification, selection policy, health state, alias fencing, restart, forwarding, and lifecycle transitions.
- [x] 4.3 Add route coverage proving A-unavailable to B-success to B-next-turn behavior and stale-header removal.
- [x] 4.4 Run formatting, lint, type, architecture, strict OpenSpec, focused, and broader regression gates.
- [x] 4.5 Complete a fresh independent multi-agent review pass with no new actionable findings.
- [x] 4.6 Add unit and route regressions for encrypted-reasoning projection, multiple retained turns, and a direct call/output split across the durable boundary.
- [x] 4.7 Re-run formatting, lint, type, architecture, strict OpenSpec, focused, and broader regression gates for the projection change.
