# Recover poisoned bridge anchors at the half-open probe

## Why

A hard-affinity HTTP bridge key whose durable `previous_response_id` anchor
upstream rejects wedges indefinitely (#1852). The retry circuit opens after
two eventless failures, and the single request admitted at each half-open
transition is planned exactly like the requests that opened it: the durable
anchor is still in place, so the bridge re-injects the same
`previous_response_id` upstream has been closing on. The probe therefore
fails identically, the circuit re-opens, and the conversation never recovers.

Three properties of the current circuit compound this:

- The anchor-poison threshold (default 7) is compared against the same
  counter the circuit gates at 2, and once open the counter only advances on
  the one admitted probe per lease, so the poison path is effectively
  unreachable from an interactive client.
- The half-open lease is a fixed 600 seconds regardless of the cooldown, so
  while probes keep failing a key answers at most one request per ten
  minutes.
- The suppression 503 derives its message and `retry_after` from the
  cooldown timer even when the cooldown has expired and the half-open lease
  is what is refusing the request, advertising `retry_after ≈ 1s` during a
  multi-minute block. Codex takes the hint literally and produces a retry
  storm (hundreds of 1–2 s apart 503s for one conversation were observed).

Observed on `1.24.0-beta.3`: direct-WebSocket conversations completed
normally in the same seconds the anchored bridge key returned
`stream_incomplete` with zero events, so this is anchor hygiene, not
upstream instability.

## What Changes

- On the transition to half-open, when the circuit's last recorded failure is
  one of the eventless poison classes, the proxy abandons the durable
  continuity anchor **before** admitting the probe, so the probe resends the
  captured full history instead of the rejected anchored request. This reuses
  the existing fenced `rebind_session_account(clear_continuity=True)` write
  and emits a `half_open_anchor_abandoned` circuit event. A `clean_close`
  last failure leaves the anchor intact.
- The half-open lease is bounded to one base backoff (60 s). A failing probe
  records a failure, which clears the lease and arms a fresh cooldown, so a
  longer lease only widens the window in which an unrecorded probe failure
  leaves the key silently suppressed.
- The suppression 503 reports whichever timer is actually refusing the
  request: `retry_after_seconds` and the logged detail now distinguish
  `hard_key_half_open` from `hard_key_cooldown`, and the message no longer
  claims the bridge is "cooling down" when it is not.

No new settings. The anchor-poison threshold setting is unchanged; the
half-open abandonment makes recovery independent of it rather than
reinterpreting it.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: the hard-affinity retry circuit's half-open probe
  becomes a real recovery experiment, its lease is bounded, and its
  suppression response is truthful about the blocking timer.

## Impact

- Code: `app/modules/proxy/_service/http_bridge/retry_circuit.py`
  (half-open transition, lease bound, block-reason helper) and
  `request_submit.py` (suppression response).
- Tests: unit coverage for anchor abandonment on poison details, anchor
  preservation on `clean_close`, block-reason reporting in both states, and
  the lease bound.
- API/schema: the suppression 503 message text changes and its
  `retry_after_seconds` becomes accurate during the half-open lease; the
  error code (`upstream_request_timeout`) and status are unchanged. No
  database or configuration change.
