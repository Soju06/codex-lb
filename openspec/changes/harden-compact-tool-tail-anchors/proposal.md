# Harden compact tool-tail anchors

## Why

Compact trimming must omit oversized ordinary tool tails without letting trim
markers create a false wire-budget failure. It must still preserve unpaired
`previous_response_id` output, pending tool calls, and records of side effects.

## What changes

- Evaluate an optional terminal tool pair with required anchors and trim-marker
  framing before retaining it.
- Preserve only unpaired continuity outputs, pending calls, and side-effecting
  tails as fail-closed compact anchors.
- Reuse the canonical tool-side-effect classifier for compact-tail safety.
- Move the resulting behavior into the live Responses API compatibility spec.

## Non-goals

- Change compaction transport, model routing, or the wire-budget limit.
