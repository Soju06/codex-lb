## 1. Account eligibility

- [x] 1.1 Let canonical additional-quota mappings defer exact account support to fresh quota telemetry while preserving plan and service-tier gates
- [x] 1.2 Cover an authoritative account catalog that omits Spark while a Pro account has fresh available Spark quota
- [x] 1.3 Prove that an explicit unrelated quota key cannot bypass model account-catalog support
- [x] 1.4 Preserve authoritative account-level service-tier exclusions for catalog-supported accounts while allowing genuine model omissions to use plan-tier fallback
- [x] 1.5 Attach normalized typed provenance only to a selected account admitted through fresh quota-backed catalog omission
- [x] 1.6 Propagate that provenance through HTTP bridge creation and account-changing reconnect, and require centralized exact model, quota-key, effective-tier, and current plan-tier compatibility before every existing-session return
- [x] 1.7 Make compatibility failure request-local: fork unanchored mismatches or fail only anchored mismatches while preserving shared live sessions, metadata, close state, and live aliases
- [x] 1.8 Keep forwarded `internal_request_parallel` compatibility forks local to the receiving canonical owner in both registered-session and in-flight-waiter mismatch paths without changing normal rendezvous ownership

## 2. Verification

- [x] 2.1 Run the focused load-balancer and additional-quota test suites
- [x] 2.2 Validate the OpenSpec change artifacts
- [x] 2.3 Reproduce the current-head two-turn bridge reuse failure, then verify one upstream transport is reused after the fix
- [x] 2.4 Cover fresh, missing, stale, and exhausted quota evidence; current plan-tier rechecks; exact provenance boundaries; direct, previous-response-alias, and in-flight-waiter reuse; and reconnect propagation
- [x] 2.5 Reproduce the in-flight prompt-cache tier mismatch and live previous-response alias loss, then verify two delayed real responses use independent transports and a later correct-tier alias request reuses its unchanged owner
- [x] 2.6 Reproduce the two-instance forwarded prompt-cache mismatch loop, then verify two real requests complete on separate transports with the creator session still open and registered
