## Why

HTTP-bridge reconnect already fails closed for every owner required to be preferred,
but a live file-pin owner is not marked as continuity provenance. Selection
therefore cannot distinguish the genuine "owner account no longer exists"
case from ordinary required-owner misses. The useful observable improvement is
an immediate existing 502 only for confirmed owner disappearance; transient
capacity misses must retain bounded recovery, and non-file required owners must
retain their existing routing eligibility.

## What Changes

- Mark only a live file-pin reconnect owner as new continuity-owner provenance;
  preserve the existing account-neutral provenance and leave other
  require-preferred owners unchanged.
- Map `continuity_owner_unavailable` early only when selection confirms that
  the owner account no longer exists.
- Preserve `hard_affinity_saturated` for transient continuity-owner misses so
  reconnect can wait and retry within its existing deadline.
- Keep file-pin owners outside dashboard single-account narrowing while still
  enforcing API-key assignment scope.
- Add regressions for transient recovery, deleted-owner mapping, and non-file
  previous-response owner routing semantics.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: HTTP-bridge file-pin reconnect immediately maps only
  confirmed owner disappearance and preserves bounded transient recovery.
- `sticky-session-operations`: file-pin provenance is typed without changing
  previous-response required-owner single-account or assignment-scope policy.

## Impact

- HTTP-bridge reconnect selection provenance and its early failure gate.
- Required continuity-owner transient error classification.
- File-pin single-account compatibility and assignment-scope eligibility.
- Focused unit coverage and OpenSpec only; no API, schema, dashboard, setting,
  create-path, affinity-write, or security-scope changes.
