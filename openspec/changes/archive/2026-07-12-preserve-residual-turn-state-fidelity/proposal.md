# Preserve residual turn-state fidelity

## Why

Turn-state continuity must retain the exact account boundary when a terminal
turn is compacted, and a collected failed response must not discard a real
upstream turn-state token that arrived before the failure.

## What changes

- Resolve compact requests carrying a real client-supplied
  `x-codex-turn-state` through the API-key-scoped bridge owner and fail closed
  when that owner is unavailable, while letting proxy-synthesized first-turn
  placeholders fall through to file-owner routing.
- Keep the established compatibility metadata-header allowlist intact.
- Surface captured upstream turn-state metadata on collected failed responses.
