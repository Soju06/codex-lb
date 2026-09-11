## Why

A completion can erase a local first strike or quarantine recorded while it loads durable state. Moving the cleanup fence earlier protects the failure but delays cleanup of durable-only poison. Separating their provenance fixes the race while preserving the existing cleanup policy.

## What Changes

- Preserve local failures recorded after completion or verified-replay origin capture, including replacement-session evidence.
- Keep main's immediate cleanup of adopted durable-only evidence after successful settlement and registration.
- Retain TTL, soft-cap and unknown-key behavior.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: local quarantine cleanup authority is independent of durable adoption.

## Impact

HTTP bridge quarantine, durable loading and completion bookkeeping. Refs #2268 for the stale-cleanup portion; unconditional memory bounds and service-wide overflow policy remain outside this change. No settings, schema or wire-format changes.
