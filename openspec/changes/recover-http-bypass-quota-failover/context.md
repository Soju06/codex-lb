# Evidence and safety boundary

The observed sequence is: a 38 MB request bypasses a 14 MB bridge budget; the
owner returns HTTP 429 before output; deterministic failover excludes the owner;
selection then rejects the same excluded account because turn-state still makes
it required. The route regression reproduces that exact state transition.

Payload size does not grant replay permission. The fallback uses the existing
durable full-resend verifier, including stored-prefix matching, retained prior
output, account-neutral item validation, API-key scope, and owner-conflict checks.
Only then are bridge affinity headers removed. The request body is already a
fresh full resend and is not rewritten.

Forwarded owner requests, previous_response_id, files, response-owned items,
incomplete history, missing durable state, and owner conflicts remain fail-closed.
Failure of the optional durable full-resend lookup is treated as unavailable
proof: the service preserves affinity and continues normal HTTP fallback, where
required owner resolution remains authoritative and fail-closed.
