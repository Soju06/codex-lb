# Fix peer 1009 send classification

## Provenance
The received-peer-1009 send gate is a LOCAL addition of the
fix/python-image-bridge-consolidation branch (a mutable trial-derived
source, not an immutable upstream commit): the trial PR2534 head
5071c18d carried the peer-1009 classification in proxy_websocket.py,
and the send-side gate was written here on top of it. See
allow-bounded-inline-images-on-bridge/consolidation.md for the
match/evolve breakdown.

## Why
Received peer close 1009 during send incorrectly becomes 502 upstream_unavailable.

## What Changes
Preserve received peer 1009 as terminal HTTP 400 payload_too_large without replay or account penalty.

## Impact
Python adapter and bridge only. No timeout, config, Native or deployment change. Historical 100-second silence remains unexplained.
