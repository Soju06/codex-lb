# Fair native event dispatch

## Why

Buffered helper output can let the reader enqueue more than the bounded queue
capacity without scheduling an already waiting body consumer. This turns a
healthy response into consumer_backpressure.

## What Changes

Yield after each accepted event; keep the existing queue bound and per-request
cancellation for stalled callers. Verify direct and routed Responses bursts.

The change owns only Python native event dispatch. It preserves request identity,
helper generation ownership, routing, and replay policy.
