# Change: Stabilize Responses Concurrency and Streaming

## Why

Sustained `/v1/responses` workloads with several long-running Codex agents can produce local 429s, occasional upstream 429s, and HTTP streaming origin timeouts. Current routing primarily uses persisted usage snapshots and local bridge/session admission state. During concurrent bursts, many requests can observe the same stale usage snapshot before upstream usage or local request pressure is reflected, causing one account or bridge session to receive disproportionate work. When bridge queues or response-create gates saturate, the service can reject locally or delay first useful bytes long enough for front-door proxies to terminate long-lived streams.

## What Changes

- Add account-level in-flight pressure accounting, leases, and per-account create/stream caps that affect account selection immediately.
- Keep account selection and lease acquisition atomic while forbidding database, network, sleep, or blocking I/O under the balancer runtime lock.
- Release leases on all streaming and non-streaming terminal paths, including failover, local errors, cancellation, timeout, downstream disconnect, and stale watchdog recovery.
- Make soft prompt-cache/sticky-thread bridge saturation reroutable to another eligible account/session, while hard continuity remains bounded and explicit.
- Normalize local 429/overload reason taxonomy and preserve upstream 429 mapping separately.
- Harden `/v1/responses` HTTP/SSE streaming for front-door proxies with anti-buffering headers, early/periodic heartbeat behavior, and low-cardinality timeout observability.
- Add regression tests and a sustained workload reproduction harness for concurrency fairness and streaming timeout behavior.

## Impact

High-concurrency Responses traffic should distribute across eligible accounts based on persisted usage plus immediate in-flight pressure. Local overload responses become easier to diagnose, soft bridge queue saturation should avoid unnecessary 429s when alternatives exist, and streaming clients/proxies should receive timely flushable bytes and explicit timeout diagnostics. Phase 1 guarantees are per proxy instance and service-wide only for single replica or multi-replica deployments with sticky ingress/deterministic owner routing; universal multi-replica fairness without affinity requires a later shared-pressure design.
