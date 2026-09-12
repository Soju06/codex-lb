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

Root turns previously bypassed the operation ledger because they had no
`previous_response_id` yet. That left only the session's latest response ID
and made every later delta-only chain appear to have a missing parent. Root
operation persistence is enabled only with complete-transcript recovery and
is scoped to the durable session to preserve duplicate fencing.

Some Responses clients include the immediately preceding tool output at the
front of the next delta (for example, a `function_call` followed by its
`function_call_output`). Replay construction must strip that echoed prefix
before adding the output to the canonical input. Retained snapshots already
contain their terminal output, so synthetic snapshot roots carry explicit
metadata to avoid appending it a second time. If the prefix is only partial or
ambiguous, replay remains fail-closed.

The client may also echo reasoning or hosted search/tool envelopes alongside
that prefix. Those item types are intentionally omitted from account-neutral
snapshots, so replay matching ignores only the known omitted types while still
requiring every retained item to match exactly.

A synthetic snapshot root already includes its retained terminal output. A
tool continuation may send only the corresponding output item, so replay
construction must not append the stored function call a second time.
