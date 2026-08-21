## Context

The durable full-resend reconciliation in the sibling change handles a
verified complete replay. A model transition is a separate path: the request
has no previous-response anchor to replay, but the durable lookup still binds
the old model's owner. When a stale hard alias disagrees, blindly selecting a
new account would be unsafe, while retrying the same alias produces a stable
502 `continuity_owner_conflict` loop.

## Decision

Keep the recovery gate inside the HTTP bridge creation loop. Inspect the typed
error code and continue only when it is `continuity_owner_conflict`; do not
reuse the broader owner-unavailable predicate. Reject forwarded requests so a
replica cannot create a local lane after an origin forwarding failure. Validate
the effective Responses payload with the existing account-neutral replay
classifier, which rejects `previous_response_id`, conversation state,
account-scoped hosted references such as `input_file.file_id`, hosted/MCP call
items, and any input item that is not self-contained. That classifier is shared
with the post-compaction recovery work, so its admitted shapes can widen over
time: a completed `compaction` item carrying its own encrypted content and
client-executed `tool_search_*` items are now accepted as self-contained, while
a compaction placeholder without that content still fails closed. File-owner
resolution and previous-response checks remain explicit defensive gates.

On success, strip session/turn aliases, replace the request key with a
server-namespaced account-neutral key, exclude the failed owner, clear the
preferred owner/provenance, and force local creation. Persisted hard aliases
are not rewritten.

The request state itself is reset to the child's own identity. It was prepared
for the parent lane before the creation loop, and the retry re-enters that loop
without rebuilding it, so the fork clears the parent affinity policy, the hard
continuity anchor, and a reused parent turn state. Leaving those set would let
the submit and clean-close paths treat the account-neutral child as the old
owner-bound turn: a stale anchor blocks the pre-output account switch that the
neutrality proof already permits, and a clean close would recover it as a
continuation of the parent turn alias.

The child lane key is pinned `hard` rather than inheriting the implicit
strength default. The sibling model-transition fresh-resend path may use a
`soft` key because it substitutes a proved full-resend projection that any
account can serve at any point. This fork forwards the client's own payload
unchanged, so once the child lane owns upstream turn state there is no verified
replay text that would make a later soft reroute to a third account safe.

## Negative Controls

- A forwarded request with the same owner conflict must return the original
  `continuity_owner_conflict` without a second creation attempt.
- A fresh payload containing an unpinned `input_file.file_id` must also return
  the original conflict without account-neutral forking.
- A post-compaction payload whose `compaction` item only references the owner's
  compacted context — no encrypted content of its own, or still in progress —
  must return the original conflict instead of forking, because the prior turns
  exist only behind the previous owner.
- A second conflict on the child lane must surface the original error instead of
  forking again.
- The forked child request must not keep the parent's affinity policy, hard
  continuity anchor, or reused parent turn state.
