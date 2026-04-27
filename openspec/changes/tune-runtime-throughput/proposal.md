# tune-runtime-throughput

## Why

Current `codex-lb` production serving on `eric-claw` used conservative runtime defaults even though the host has multiple CPU cores and the runtime already ships with `uvloop` and `httptools`. That leaves safe throughput headroom unused for concurrent Codex CLI traffic. The deployment also left Codex HTTP bridge prewarm and Codex-specific HTTP fail-fast behavior too conservative, which increases cold-start and bad-tail latency for bridge-backed Codex requests.

## What Changes

- add explicit runtime serving knobs for `uvicorn` workers, loop, and HTTP parser
- document those knobs in `.env.example`
- make the default worker assumption multi-core aware instead of single-process by default
- enable Codex HTTP bridge prewarm by default
- add a shorter Codex-specific HTTP bridge request budget
- raise HTTP connector defaults modestly
- enable a higher-throughput runtime profile on the `eric-claw` deployment

## Impact

- improved concurrency headroom on multi-core hosts without changing default single-worker behavior for other environments
- lower first-turn latency for bridge-backed Codex requests
- lower worst-case Codex HTTP bridge tail latency
- no API contract change
