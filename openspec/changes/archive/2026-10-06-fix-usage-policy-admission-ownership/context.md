## Purpose and scope

Close the remaining admission and cancellation gaps found in PR #1528 without replacing its shared evaluator. The normative delta is in specs/account-routing/spec.md.

## Failure examples

An unbound recovery account with usage 10 and cap 20 can pass an early generation fence, wait for the probe lock, then return after an operator lowers its cap to 10. Initial process-seed persistence creates the same final-await gap. A rejected WebSocket frame removed from the pending queue can lose its only cleanup owner when cancellation interrupts lease release.

## Constraints and decisions

Keep already-dispatched work and committed affinity owned. Use cancellation-deferring resource release and tracked finalization tasks already present in the proxy. Do not collapse standard and additional quota, canonical error evidence and capacity, or transport-specific retirement rules.

## Verification and operations

Preserve the existing regressions and add deterministic policy-edit and cancellation schedules. Check no unauthorized upstream send, complete reservation/lease/gate settlement, and unchanged affinity. Retain the published migration graph; no database upgrade is introduced by this correction.

## Contract verification

| Dimension | Evidence |
| --- | --- |
| Completeness | Six implementation/verification/preparation tasks completed; external publication and monitoring remain delivery actions. |
| Correctness | Both requirements and all four scenarios are covered by admission-fence and cleanup regressions. |
| Coherence | Probe commitment keeps its runtime CAS; seed admission carries the successful generation; WebSocket rejection uses tracked finalization; late selection cleanup reuses cancellation deferral. |

The HTTP probe and process-seed cases exercise the actual Responses route and acknowledged policy PUTs. The WebSocket cancellation case uses the actual proxy loop, authoritative owner reads, and a persisted limited API-key reservation; it checks no upstream send, released database accounting, gate and lease settlement, and one terminal denial. The sticky tests check returned owner, quiet interval, affinity, and stream/token pressure. The successful retry test preserves ordinary disabled-policy seeding.

With only the new tests copied into a detached checkout of `0db3a2235753d8dd819a04568cc10b5f09cd7e8e`, six failing schedules reproduce the admission or settlement defects and the successful-retry case passes. All seven pass with the correction. Existing regression tests and assertions are retained.

## Local validation results

- Routing/contract/concurrency, Responses, migration, and migration-tool suites: 830 passed, 11 PostgreSQL-only skips.
- WebSocket terminal cancellation, HTTP bridge idle leases, and both transport integration suites: 454 passed.
- Additional WebSocket unit coverage: 436 passed.
- Historical populated-parent upgrades and branch-specific direct downgrades: 4 passed.
- Lint, typing, Rust formatting/clippy/tests/release build/dependency audit, frontend lint/types/build, and wheel/frontend-asset verification passed.
- Strict validation passed the correction and all 67 main specifications. Topology against freshly fetched upstream `f8ffbac2099a113fba54dfd8d77774f5bca80ffa` reports 267 revisions and one head, `20261002_010000_merge_usage_limit_review_heads`; SQLite upgrade/check reports no schema drift. No migration file is changed by this correction.

The required local CI invocation stopped during frontend coverage: 1,702 tests passed and two failed (API-key rendering wait and firewall timeout). Both cases passed in a six-test run without coverage and failed again with coverage enabled. This correction changes no frontend source. The full local gate is not green; full coverage and the remaining cloud-only PostgreSQL/deployment checks must be verified on the published head. Local PostgreSQL is unavailable, and Trivy, Helm, kubeconform, and kind are absent. These limitations do not change assertions or justify claiming PR readiness.
