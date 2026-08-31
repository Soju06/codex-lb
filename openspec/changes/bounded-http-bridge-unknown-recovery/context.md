## Context

The upstream service does not expose a reliable idempotency key or request
status endpoint. A local operation row is therefore the only delivery fence.
This change improves recovery only when the bridge can identify one retained
turn inside the same hard-continuity session; it deliberately does not turn an
ambiguous request into an unconditional replay.

The 15-minute creation window and eight-candidate bound are conservative
defaults. A retry of an old operation eventually falls back to the existing
fail-closed response, while multiple matching turns remain ambiguous. The
existing parked-recovery flag is the operator opt-in and remains false by
default in settings.
