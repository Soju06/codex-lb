The Responses stream is authoritative at the event level: output items are
completed before `response.completed`, and some Codex streams intentionally
leave the terminal response's `output` array empty. The event spool is already
durable and bounded, so using it at terminal settlement avoids adding a second
large in-memory transcript and keeps the feature's existing opt-in and
fail-closed safety properties.

The proxy also persists a bounded account-neutral input snapshot containing the
sanitized current request history and terminal output. A later continuation can
use that snapshot as a fresh `response.create` body when the upstream parent
response chain has been purged. Snapshot creation is best effort and remains
behind the existing opt-in recovery setting.
