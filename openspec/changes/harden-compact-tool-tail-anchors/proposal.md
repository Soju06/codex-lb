# Harden compact tool-tail anchors

## Why

Compact trimming must omit oversized ordinary tool tails without letting trim
markers create a false wire-budget failure. It must still preserve tool output
that is anchored by `previous_response_id` and records of `apply_patch` side
effects.

## What changes

- Evaluate an optional terminal tool pair with required anchors and trim-marker
  framing before retaining it.
- Preserve continuity-anchored output-only deltas and apply-patch tails as
  fail-closed compact anchors.
- Move the resulting behavior into the live Responses API compatibility spec.

## Non-goals

- Change compaction transport, model routing, or the wire-budget limit.
