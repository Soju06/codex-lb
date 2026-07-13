# Change: Harden Responses Lite follow-up boundaries

## Why

The canonical Responses Lite transport landed in #1161. Adjacent transformations can still corrupt or overrun that valid request shape: compact trimming can drop required prelude state, post-validation image inlining can exceed the upstream wire budget, API-key enforcement can rewrite Lite input onto a non-Lite model, and reconnect replay can duplicate code-mode side effects.

## What Changes

- Preserve required Lite compact state and measure the final serialized input, including Unicode escaping, array framing, and inlined images.
- Return the standard client-payload error before admission or upstream work when compact input cannot fit, and release any API-key reservation on a late transformed-input rejection.
- Reject API-key model rewrites that target a catalog-confirmed non-Lite model.
- Deduplicate replayed code-mode side effects without collapsing distinct calls that happen to use identical source text.

## Impact

- Affected spec: `responses-api-compat`.
- Affected code: compact request preparation, request policy, API admission, and tool-call replay deduplication.
- The body-derived Lite signal and trusted continuity rules from #1161 remain unchanged.
