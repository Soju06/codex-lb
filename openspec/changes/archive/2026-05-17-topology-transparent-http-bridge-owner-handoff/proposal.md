# Topology-Transparent HTTP Bridge Owner Handoff

## Why

`/responses` HTTP bridge continuity currently leaks replica topology when hard session continuity keys land on the wrong replica. Prompt-cache locality is already treated more softly, but true turn-state/session continuity still returns `409 bridge_instance_mismatch` instead of preserving the session internally.

## What Changes

- distinguish hard continuity keys from soft locality keys on the HTTP bridge session key itself
- forward hard-key owner mismatches to the owner replica through a dedicated internal bridge endpoint
- advertise replica bridge endpoints through ring membership metadata so owner selection can resolve to a routable endpoint
- add owner-forward observability for mismatch, rebind, latency, and success/failure outcomes

## Impact

- clients no longer need replica awareness to preserve `/responses` continuity
- prompt-cache locality misses remain soft and local
- turn-state/session continuity survives wrong-replica arrival for both streaming and non-streaming `/responses`
