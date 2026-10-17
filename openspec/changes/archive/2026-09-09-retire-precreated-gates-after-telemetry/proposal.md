## Why

Issue #1206 shows HTTP bridge sessions remaining wedged behind a
`response_create_gate` for more than 800 seconds even though the configured
stuck-gate retirement threshold is 300 seconds. The upstream Codex protocol can
emit `codex.rate_limits` before `response.created`. That telemetry is attributed
to the sole pending request and records first-upstream-event latency, but it
does not assign a response ID or release the response-create gate. The current
retirement predicate incorrectly treats that latency marker as proof of request
progress, so every later gate waiter skips retirement and the session remains
blocked until process restart.

## What Changes

- Treat a pre-created HTTP bridge request as stuck based on the protocol
  milestone that owns the gate: whether `response.created` arrived.
- Do not let leading non-visible telemetry such as `codex.rate_limits` suppress
  stale-gate retirement.
- Keep pre-created `response.*` lifecycle activity protected from retirement,
  even before text becomes visible.
- Keep the existing transport, visible-request, gate-ownership, downstream
  visibility, response-created, and age safeguards unchanged.
- Transparently submit the still-unsubmitted hard-affinity waiter on a fresh
  bridge after it retires the stale owner, while preserving its original
  request deadline and any previous-response account pin.
- Add a bridge-path regression that processes real leading rate-limit telemetry
  before exercising the stale-gate timeout.

## Impact

- Affected capability: `proxy-admission-control`.
- HTTP bridge sessions whose upstream sends telemetry but never creates a
  response self-heal after the configured retirement threshold.
- Healthy created or downstream-visible streams remain protected from
  retirement.
- Pre-created streams emitting response lifecycle events remain protected from
  retirement.
- The waiter that discovers and retires a stale hard-affinity owner no longer
  needs a client reconnect when its upstream acceptance boundary is provably
  untouched.
