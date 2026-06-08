## Tasks

- [x] Capture baseline code evidence for current `/v1/responses` concurrency, bridge, admission, and streaming timeout touchpoints.
- [x] Add OpenSpec delta requirements for account leases/caps, bridge soft-affinity reroute, stable overload taxonomy, and streaming timeout hardening.
- [x] Add account pressure configuration with safe defaults and environment aliases.
- [x] Implement account-local runtime lease state, pressure scoring, atomic select+lease, and stale lease reclamation without blocking under the balancer runtime lock.
- [x] Integrate lease acquisition/release with streaming and non-streaming `/v1/responses` paths, including failover/error/cancel/timeout/downstream disconnect paths.
- [x] Add per-account response-create and stream caps to candidate eligibility and local overload diagnostics.
- [x] Implement soft-affinity bridge reroute before queue/gate saturation where no hard continuity anchor exists.
- [x] Preserve hard continuity behavior with explicit bounded local failure reasons.
- [x] Add anti-buffering SSE headers, early heartbeat/flush behavior, and timeout/heartbeat observability for `/v1/responses` streams.
- [x] Add targeted unit/integration tests for fairness distribution, caps, lease cleanup, taxonomy, bridge reroute, hard continuity, streaming headers, and heartbeat behavior.
- [x] Add or update a sustained workload reproduction harness for 5-6 agent/hour style load without requiring production credentials in the repo.
- [x] Run OpenSpec validation, targeted pytest, and lint/format checks.
- [x] Run final cleanup and independent review gate before final Ultragoal completion.
