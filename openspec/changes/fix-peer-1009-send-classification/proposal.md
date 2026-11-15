# Fix peer 1009 send classification

## Provenance
Ported from trial PR2534 head 5071c18d (peer1009 classification in
proxy_websocket.py) by the fix/python-image-bridge-consolidation branch;
see allow-bounded-inline-images-on-bridge/consolidation.md.

## Why
Received peer close 1009 during send incorrectly becomes 502 upstream_unavailable.

## What Changes
Preserve received peer 1009 as terminal HTTP 400 payload_too_large without replay or account penalty.

## Impact
Python adapter and bridge only. No timeout, config, Native or deployment change. Historical 100-second silence remains unexplained.
