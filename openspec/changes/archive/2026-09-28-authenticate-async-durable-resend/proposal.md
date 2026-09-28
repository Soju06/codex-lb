## Why

A caller can relabel a synchronous upstream tool call as asynchronous in a full resend. The durable pending-tool manifest still records it as synchronous, but the retained-assistant-output branch accepts the caller's marker without comparing provenance and can replay an incomplete transcript to a different account after owner loss.

## What Changes

- Authenticate marked-asynchronous IDs against the durable synchronous pending-tool manifest before either owner-loss proof can succeed.
- Clarify the existing verified-full-resend requirement: genuine asynchronous work does not block a completed-assistant boundary, and a delayed typed result counts as fresh follow-up input only with retained prior output.
- Document why stateless replay accepts self-contained asynchronous call items without a durable manifest: it has no previous owner or omitted upstream state to authenticate.
- Verify that answered WebSocket asynchronous calls leave the pending map through the shared request preparation path; keep independent cleanup calls explicit.

## Impact

Only Responses replay and asynchronous tool continuity, its owning tests and `responses-api-compat` OpenSpec contract change. No new settings, migrations, or sibling PR features.
