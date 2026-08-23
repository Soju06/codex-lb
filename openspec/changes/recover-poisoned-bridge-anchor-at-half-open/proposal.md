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

- When the hard-affinity retry circuit opens on an eventless poison-class
  failure (`stream_incomplete` / `stream_idle_timeout`), the proxy quarantines
  the bridge key with reason `retry_circuit_poisoned_anchor`. The existing
  quarantine path (#1534) then plans the next full-resend request on that key
  **unanchored** — the fresh-reattach injection, durable hydration, and
  session-level injection are all suppressed — so the probe admitted after
  the cooldown resends the client's full history instead of the anchor the
  circuit opened on. Delta-only payloads keep their anchor, exactly as the
  quarantine requirement already specifies, because it is their only way to
  convey prior context. Quarantine keeps its existing TTL, registry bound,
  and clear-on-completion semantics, and still writes no account health.
- An upstream terminal error frame that fails a pending request before any
  response event now records an attempt-scoped circuit strike. Such failures
  settle through the terminal path rather than the retirement funnel and
  previously never advanced the circuit at all — in the observed incident,
  five eventless `previous_response_not_found` terminal frames on one key
  left the persisted counter at `1`, so neither the circuit nor the
  quarantine above could ever engage while the bridge kept re-injecting the
  dead anchor.
- The suppression 503 reports whichever timer is actually refusing the
  request: `retry_after_seconds` and the logged detail now distinguish
  `hard_key_half_open` from `hard_key_cooldown`, and the message no longer
  claims the bridge is "cooling down" when it is not.

The half-open lease is unchanged: it bounds the probe's lifetime so exactly
one probe runs per transition, and shortening it would admit concurrent
continuations on a hard key while a long probe is still streaming.

No new settings. The anchor-poison threshold setting is unchanged; the
circuit-open quarantine makes recovery independent of it rather than
reinterpreting it.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: the hard-affinity retry circuit's half-open probe
  becomes a real recovery experiment, its lease is bounded, and its
  suppression response is truthful about the blocking timer.

## Impact

- Code: `app/modules/proxy/_service/http_bridge/retry_circuit.py`
  (circuit-open quarantine, block-reason helper), `quarantine.py` (reason),
  and `request_submit.py` (suppression response).
- Tests: the circuit-open quarantine on a poison detail and its absence on
  `clean_close`; a product-path test that opens the circuit with two
  eventless failures and asserts the next full-resend request is planned
  without the durable anchor; block-reason reporting in both states.
- API/schema: the suppression 503 message text changes and its
  `retry_after_seconds` becomes accurate during the half-open lease; the
  error code (`upstream_request_timeout`) and status are unchanged. No
  database or configuration change.
