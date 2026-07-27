## 1. Bounded automatic recovery

- [x] 1.1 Add deterministic oldest-due probing-account admission to health-tier-aware selection while preserving recent-probe healthy preference and non-health-tier strategies.
- [x] 1.2 Add focused pure-selection tests for due/recent probing accounts and fair deterministic rotation.
- [x] 1.3 Add load-balancer coverage proving a selectable existing sticky owner is not displaced by a due probing account.
- [x] 1.4 Preserve hard-sticky fail-closed ownership while cap-filtering fallback candidates, and atomically reserve concurrent fallback probes.
- [x] 1.5 Preserve the stable local cap error when an unavailable hard-sticky owner has only saturated fallbacks.
- [x] 1.6 Keep sticky probe reservations provisional through final lease and persistence gates, and classify cap exhaustion using only otherwise-available fallbacks.
- [x] 1.7 Reject stale probe reservations with a runtime-version CAS and defer probe affinity until that CAS commits.
- [x] 1.8 Classify hard-sticky cap exhaustion over complete fallback pools and preserve local cap errors for opportunistic traffic.
- [x] 1.9 Defer sticky mutations through admission, reject backoff-only cap bypass, and preserve hard-sticky ownership on local cap errors.

## 2. Force Probe health settlement

- [x] 2.1 Centralize replica-local health-tier/timestamp transitions so ordinary selection and explicit probe settlement use one state machine.
- [x] 2.2 Add load-balancer and proxy-service settlement for Force Probe results using refreshed account status/usage, counting only HTTP 2xx and resetting unsuccessful streaks.
- [x] 2.3 Connect the accounts probe API to process-local settlement without changing the endpoint response contract.
- [x] 2.4 Add unit and integration regressions for successful rehabilitation, rejected probes, usage pressure, and endpoint orchestration.
- [x] 2.5 Reject stale successful probe settlement when newer replica-local runtime activity was recorded during snapshot loading.
- [x] 2.6 Share zero-primary-capacity health normalization between ordinary routing and Force Probe settlement.
- [x] 2.7 Keep provisional probe reserve/release outside the runtime health-observation version used by Force Probe settlement.

## 3. Verification

- [x] 3.1 Run focused balancer and account probe test suites, lint, formatting, and type checking.
- [x] 3.2 Run strict validation for the change and all OpenSpec specs, then verify implementation coverage against the artifacts.
