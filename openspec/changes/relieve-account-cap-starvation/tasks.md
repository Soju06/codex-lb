# Tasks

## 1. Ring heartbeat inflight publication

- [x] 1.1 Publish per-account in-flight stream-lease counts in bridge-ring heartbeat metadata alongside the advertised endpoint.
- [x] 1.2 Add a ring reader returning fresh peers' published counts keyed by instance id.
- [x] 1.3 Refresh a process-wide peer-count snapshot on the same heartbeat tick that refreshes the cap partition.

## 2. Stream-share borrowing

- [x] 2.1 Compute a per-account borrow allowance (`floor((cap − observed cluster in-flight) / R)`, floored at 0) gated on fresh counts from every other active member.
- [x] 2.2 Apply the allowance in the sticky-selection cap filter and the lease admission check for stream leases only.
- [x] 2.3 Record borrowed-lease admissions in a metric.

## 3. Bounded account-capacity wait

- [x] 3.1 Add a fixed 120-second account-capacity wait ceiling (no new setting; simplicity gates).
- [x] 3.2 Clamp the bridge capacity-wait plan for account-capacity errors to the ceiling while leaving `response_create_gate_timeout` waits budget-bounded.
- [x] 3.3 Surface the original HTTP 429 cap envelope when the ceiling expires.

## 4. Unanchored parallel fork spillover

- [x] 4.1 On a local account-cap error during unanchored parallel fork session creation with a self-contained payload, drop the preferred-account hint once and retry selection.
- [x] 4.2 Log the spill with a stable bridge event.

## 5. Tests

- [x] 5.1 Borrow-allowance math, freshness gating, and admission behavior.
- [x] 5.2 Ring metadata round-trip for published counts.
- [x] 5.3 Capacity-wait ceiling clamping and legacy `0` behavior.
- [x] 5.4 Unanchored fork spill predicate coverage.
