# Anonymous output ownership

The fix covers one created response alongside siblings still awaiting creation. For example, when A has received `response.created` and B has only sent `response.create`, an id-less text delta belongs to A. Both relay accounting and direct WebSocket archive attribution need the event type to select A.

The archive path runs before relay processing and uses the same parsed frame. Omitting its event type silently selected B under the legacy matching rules while relay processing selected A. Forwarding the existing parsed event type fixes that disagreement without a second parser or ownership algorithm. Regression coverage observes the archive request context, relay accounting, and unchanged downstream bytes, with A visible and with A marked draining.

Anonymous `response.completed` is a terminal, not output for this preference. With two visible requests, created A and unacknowledged B, the existing terminal matcher selects B; the correction preserves that removal, finalization and archive attribution. This restores pre-change semantics, not a guarantee of the true owner of every id-less terminal. Failed/incomplete terminals and drain preferences remain unchanged.

The direct WebSocket relay forwards on one shared downstream socket; this correction does not introduce drain-based suppression. HTTP bridge downstream cancellation continues to use the existing per-request queue lifecycle.

Multiple-created ambiguity is outside this correction. The new rule returns early only for exactly one known response id; all other counts retain the base matcher. In particular, one created drain plus one created visible request still selects the visible request. That conditional hazard is inherited, not a guarantee that the arrangement cannot occur. Changing it to fail closed needs a separate supported behavior decision and evidence of the relevant upstream ordering; narrowing the specification did not fix that potential runtime hazard.

The earlier Docker image was built from `f5936a403ea900312549fae778404c3b050b20ce` (tree-identical signed replacement `b2d0cc21177e1fcb94804582f18af792798a12b4`). It does not contain the archive or terminal correction. Its HTTP bridge results do not validate direct WebSocket archive attribution.
