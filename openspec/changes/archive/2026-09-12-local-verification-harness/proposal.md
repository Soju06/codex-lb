# Reusable local verification harness

## Why
The compaction rollout exposed three verification weaknesses: probes could start before replacement readiness, repository fixtures could import modules absent from the deployed baseline, and slow probes emitted no progress or durable partial results. One-off probes also duplicated SSE parsing and did not test cross-replica summary replay.

## What Changes
Add a reusable local verification CLI with sequential readiness/inference gates, bounded HTTP/SSE and WebSocket validation, optional compaction/replay across replicas, stage reports and progress. Add an isolated release-test runner with an explicit fixture baseline and verified application import path. Reuse the probe from the existing stable-entry validator without changing its environment contract. Provide a read-only idle check that reports incomplete telemetry rather than crashing or inferring readiness.

## Impact
Operator scripts and deterministic harness tests. No serving-code changes, schema changes, restarts, routing mutations, or production test-database use.
