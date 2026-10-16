## Why

A conversation requiring one account can fail as owner-unavailable while that
ACTIVE account serves only a short transient-error backoff. Following a denied
proxy anchor, the lack of replay metadata also prevents migration, leaving that
thread reconnecting while another thread on a healthy sibling keeps working.

## What Changes

- Reuse the hard-owner selection implementation proposed in #2486, retaining
  its author attribution and its explicit distinction between hard ownership
  and soft locality. Do not add a second competing fallback implementation.
- Add HTTP-route regressions for the observed compound failure: anchor denial,
  cleared metadata, transient owner backoff, and an otherwise healthy sibling.
- Require recovery on the original account with the client's full history
  unchanged; preserve real quota/status exclusions and client/file ownership.
- Document the selection guarantee in OpenSpec. If #2486 merges first, drop
  the identical implementation hunks and keep the additional contract/coverage.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sticky-session-operations`: a required owner's bounded transient-error
  backoff does not make its conversation ownerless or authorize migration.

## Impact

Core account selection, sticky/unbound selector propagation, HTTP bridge route
regressions, and the corresponding OpenSpec requirement/context. No schema,
setting, dependency, public route, or dashboard change. Separate from #2626.
Partial fix for #1707; follow-up to #2486.
