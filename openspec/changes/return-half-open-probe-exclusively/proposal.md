# Return half-open probes exclusively

## Summary

Repair the hard HTTP bridge retry circuit's half-open recovery without carrying
the neighboring session-recovery, quarantine, denied-anchor, or attribution
vehicles.

## Why

An absent, zero, or negative durable cooldown must not be reconstructed as a
non-zero monotonic deadline. A positive deadline that elapsed before loading is
different: it records a real cooldown transition, so exactly one local request
must consume it as a half-open probe. When a real half-open probe is consumed by
this proxy's own continuity-ownership loss, the probe must be returned without
charging the upstream circuit. Returning it must still preserve single-flight
admission for the next local reconnect, and proxy-owned teardown must not
manufacture an upstream failure while the reset is in progress.

## What changes

- Normalize absent, zero, negative, and elapsed durable cooldown deadlines to
  the zero sentinel while retaining real future cooldowns and durable failure
  counts. A newly loaded positive elapsed deadline carries a one-shot local
  transition, retained as long as its ever-claimed durable row can survive, so
  concurrent or repeated loads cannot manufacture additional probes.
- Track the process-local session that acquired a half-open probe. Only that
  owner may return it, and a returned probe becomes an elapsed cooldown so the
  next local admission acquires a fresh lease.
- Mark pending sends disarmed and detach the retiring session under the existing
  lifecycle/bridge ownership locks before returning a probe. Complete reset
  cleanup through a cancellation-shielded path.
- Classify explicitly identified continuity-ownership loss as proxy-side
  recovery, not upstream failure. Genuine upstream eventless failures keep
  incrementing the circuit.

## Scope and non-goals

This change owns only the half-open probe return contract. #1947 remains the
vehicle for cooldown-suppressed session retirement; #1891 owns poisoned-anchor
quarantine; #1902 owns denied-anchor retirement and is the sole attribution
carrier; #1867 remains the broad anchor-recovery vehicle. This change adds no
anchor replay/provenance policy, attribution file, migration, setting, or
durable schema change.

The durable row remains the replica-wide source for failure counts and real
cooldown deadlines. The active half-open owner is intentionally process-local:
only the process that owns the upstream socket can prove which probe and
pending attempts it is releasing. Replicas therefore share cooldown/failure
state but do not claim an in-flight local lease through a new schema.

## Affected capability

- `responses-api-compat`: hard HTTP bridge retry-circuit recovery.
