# Correct observed TTFT and generation TPS

## Why

Request-list and report TPS currently use total request latency and a reasoning-inclusive TTFT. Local completion work and reasoning can therefore change the displayed non-reasoning speed. Missing report samples also appear as measured zeroes. This change resolves #2443 using the existing upstream terminal clock without changing total latency or routing cohort semantics.

## What Changes

- Persist upstream terminal timing independently and capture non-reasoning output evidence at upstream observation.
- Qualify backend TPS estimates and distinguish historical or unavailable samples.
- Use qualified samples and nullable medians in request lists and reports.
- Preserve optional source reasoning/timing evidence and parse fragmented SSE metrics safely.

## Impact

Proxy observations, additive request-log schema, dashboard/report metrics, synthetic regression coverage. No new settings or required setup.
