# Why

The HTTP bridge can lose its upstream WebSocket before receiving
`response.completed` even though a bounded, durable transcript is available.
The existing recovery hook only handles an explicit
`previous_response_not_found` event, so a zero-event transport close remains a
client-visible 502 and cannot continue an old session.

# What Changes

- Allow the existing opt-in complete-transcript recovery flag to attempt a
  bounded unanchored replay after an eventless transport close.
- Keep the attempt behind an explicit call-site guard, a hard-session durable
  operation fence, and the existing account/duplicate-execution checks.
- Normalize exact duplicate tool echoes and tool-call/output pairs produced by
  compacted client history while rejecting conflicting call IDs.
- Leave the default fail-closed behavior unchanged when the flag is disabled
  or the transcript cannot be proven self-contained.

# Capabilities

## Modified Capabilities

- `responses-api-compat`: eventless HTTP bridge closes can use a durable,
  account-neutral transcript replay when explicitly enabled.

# Impact

Recovery remains opt-in and at-least-once for the upstream request: an
eventless close does not prove that the upstream did not execute. The durable
operation rebind, single replay budget, account pinning, circuit-generation
claim, transcript bounds, and self-contained tool settlement validator remain
required. If any proof fails, the proxy returns the existing fail-closed error.
