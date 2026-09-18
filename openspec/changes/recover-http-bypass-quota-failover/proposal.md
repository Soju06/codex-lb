## Why

When a full-history request exceeds the HTTP bridge WebSocket payload budget,
the raw HTTP path retains the bridge turn-state owner. A pre-visible 429 excludes
that account but the next selector still requires it, so failover stops while
other accounts remain healthy.

## What Changes

- Verify the full resend against API-key-scoped durable bridge metadata before
  releasing turn-state ownership on the raw HTTP fallback.
- Remove bridge affinity headers only after the request is proven account-neutral.
- Preserve hard ownership for incomplete history, explicit response anchors,
  files, account-owned items, forwarding, and conflicting owner evidence.

## Impact

The change affects only bridge-bypassed Responses requests. It adds no setting,
dependency, schema change, dashboard surface, or setup step. It complements the
unanchored quota replay work in #2069 rather than replacing it.
