## Context

The durable full-resend reconciliation in the sibling change handles a
verified complete replay. A model transition is a separate path: the request
has no previous-response anchor to replay, but the durable lookup still binds
the old model's owner. When a stale hard alias disagrees, blindly selecting a
new account would be unsafe, while retrying the same alias produces a stable
503 loop.

## Decision

Keep the recovery gate inside the HTTP bridge creation loop. Inspect the typed
error code and continue only when it is `continuity_owner_conflict`; do not
reuse the broader owner-unavailable predicate. Reject forwarded requests so a
replica cannot create a local lane after an origin forwarding failure. Validate
the effective Responses payload with the existing account-neutral replay
classifier, which rejects `previous_response_id`, conversation state, opaque
hosted inputs, and other account-bound fields. File-owner resolution and
previous-response checks remain explicit defensive gates.

On success, strip session/turn aliases, replace the request key with a
server-namespaced account-neutral key, exclude the failed owner, clear the
preferred owner/provenance, and force local creation. Persisted hard aliases
are not rewritten.

## Negative Controls

- A forwarded request with the same owner conflict must return the original
  `continuity_owner_conflict` without a second creation attempt.
- A fresh payload containing an unpinned `input_file.file_id` must also return
  the original conflict without account-neutral forking.
