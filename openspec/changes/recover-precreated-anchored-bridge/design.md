## Context

The eventless watchdog already owns the pending request under the session
lifecycle lock, cancels the old receive task, and invokes
`_retry_http_bridge_precreated_request` before terminal retirement. The generic
replay predicate intentionally rejects an anchored continuation because a
second ambiguous submission can duplicate a turn. That predicate is too broad
for the narrower watchdog state: a hard owner has not observed any upstream
response event, and the request has no downstream-visible output.

## Decision

Add a call-site-only opt-in to the existing pre-created retry path. The opt-in
is accepted only when the session key is hard and the request satisfies a new
pure predicate for the zero-event, pre-`response.created` same-anchor case. The
retry remains on the established account and carries the existing anchor; it
does not clear continuity, select another account, or use a fresh unanchored
replay body.

The recovery budget is one additional dispatch for this watchdog event. The
existing `replay_count` and clean-close controls remain authoritative, and the
predicate refuses a second attempt. If reconnect or resend fails, the current
fail-closed retirement path settles the request and releases its gate and
reservation exactly as before.

## Explicit exclusions

- No default-on indefinite recovery or multi-account replay.
- No retry after `response.created`, any response event, model output, or
  downstream sequence/output.
- No changes to durable recovery journal semantics or operation settlement.
- No changes to direct WebSocket behavior.
