## Purpose and evidence

A native HTTP client repeatedly resent 244 items. The proxy injected an anchor
using a turn-state durable row, trimmed the input to four items, and then
refused dispatch on a different thread key with `bridge_previous_response_not_found`.
Read-only inspection confirmed two same-account durable records: the old
turn-state row retained the anchor/prefix, while the thread row had cleared
anchors and an `anchor_abandoned` circuit tombstone. Repeating the client
request therefore repeated the server's contradictory plan.

## Example and constraints

Turn state T resolves to old row A; native headers identify thread B. Both
belong to the same account and API-key scope. B has a poisoned anchor, and
the local T alias is absent, so acquisition falls back to B. Before planning
an anchor from A, check B's fence and forward the client's complete request
unchanged on its resolved owner. A one-item delta still cannot reconstruct
missing context, and a client-supplied anchor is never silently removed.

The production database was read only. Tests seed the historical layout in
isolated SQLite. No user conversation content or credentials are included.
This is separate from #2627's transient-backoff selection and from #2591's
WebSocket required-owner connect behavior.
