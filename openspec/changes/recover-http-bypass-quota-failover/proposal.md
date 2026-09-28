## Why

When a full-history request exceeds the HTTP bridge WebSocket payload budget,
the raw HTTP path retains the bridge turn-state owner. A pre-visible 429 excludes
that account, but `_stream_owner_bound_to` still treats any turn-state owner as
bound, so the quota rejection surfaces while other accounts remain healthy. An
owner already benched for quota fails the request with no dispatch at all.

## What Changes

- Send the first attempt to the owner with all session and turn-state aliases.
- Pass a lazy durable full-resend verifier from the bypass branch into
  `_stream_with_retry`; run it only on quota evidence.
- Fill the existing verified-replay state with the bridge's account-neutral
  projection, and route every owner-move site through one predicate.
- Strip the aliases for the replacement dispatch only.
- Keep hard ownership for incomplete history, explicit response anchors, files,
  forwarding, owner disagreement, non-quota failures, and visible output.

## Impact

The change affects only bridge-bypassed Responses requests whose turn-state
owner is lost to quota. It adds no setting, dependency, schema change,
dashboard surface, or setup step. It conflicts with the pending #2374 proposal
line that turn-state ownership always declines relocation; `context.md` sets out
that conflict for a maintainer decision.
