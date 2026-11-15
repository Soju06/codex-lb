# Retain the upstream reset deadline in deferred keyed health penalties

## Why

The HTTP bridge classifies a pre-created upstream `usage_limit_reached` /
`rate_limit_exceeded` event as an account penalty while the request's API-key
reservation is still open, so the health write is queued and applied only after
reservation settlement (settlement-ordering invariant). The three pre-created
branches rebuilt the queued penalty payload as `{"message": ...}`, dropping the
upstream `resets_at` / `resets_in_seconds` fields. When settlement later applied
the penalty, `handle_rate_limit` found no reset metadata and fell back to the
no-metadata floor (30s) instead of the real deadline: an upstream hold of e.g.
14400s degraded to a 30-second hold, so an exhausted account re-entered the
routable pool and hit the same wall repeatedly. This also violates the existing
account-routing requirement that usage-limit penalties retain the persisted
reset deadline.

## What Changes

- Normalize a pre-created upstream error event into the queued health penalty
  anchored at the observation time: a valid absolute `resets_at` is kept
  verbatim, a valid relative `resets_in_seconds` is frozen to an absolute
  deadline against the rejection-time clock, and the relative duration is not
  carried across the defer (re-anchoring it at settlement time would stretch
  the hold by the whole settlement delay and could resurrect an elapsed hold).
  Numeric, non-boolean validation mirrors `_upstream_error_from_openai` and
  `_extract_reset_at`.
- Keep the settlement-ordering invariant unchanged: the penalty is still queued
  while the reservation is open and still applied only after settlement or
  fallback release. Only the queued payload is enriched; classification, defer
  timing, account ownership, and failover behavior are untouched.
- Add regression coverage at the bridge path asserting the queued penalty
  carries the upstream `resets_at` / `resets_in_seconds`.

## Capabilities

### Modified Capabilities

- `account-routing`: a usage-limit / rate-limit penalty retains the upstream
  persisted reset deadline on every write path, including the deferred
  keyed-health path.

## Impact

- Affected code: `app/modules/proxy/_service/websocket/helpers.py` (new
  normalizer), `app/modules/proxy/_service/http_bridge/upstream_events.py`
  (three pre-created branches), `app/modules/proxy/_service/http_bridge/service_stubs.py`
  and `app/modules/proxy/service.py` (re-exports).
- No routing-policy, dashboard, schema, or CLI change; no new settings.
- Upstream-facing effect: exhausted accounts now observe the real hold instead
  of a 30-second floor, so the same account is not re-selected within the
  upstream window.
