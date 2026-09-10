## Why

Issue #1975 documents a missed warm-up when live ingestion persists a confirmed reset before the scheduler reads its baseline. A freshness-skipped poll loses the transition even though the database retains the evidence.

## What Changes

- Evaluate persisted reset evidence for the selected account even when polling writes nothing.
- Reuse durable attempt claims to consume each reset at most once across scheduler restarts and duplicate snapshots.
- Keep current quota availability, plan/window selection, opt-in and reset confirmation rules.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: Recover reset-confirmed warm-up from persisted usage evidence independently of the poll writer.

## Impact

Backend scheduler and usage-history reads, with integration tests through live ingestion and scheduler lifecycle. No frontend, new setting, upstream dependency or runtime deployment.
