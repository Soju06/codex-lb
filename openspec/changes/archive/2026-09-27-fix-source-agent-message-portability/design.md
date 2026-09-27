## Context

The 2026-09-27 incident was reproduced on all three HA backends. A subscription parent delegated to a custom source; the client generated an `agent_message` with a local ID, author, recipient, text wrapper and encrypted task content. The pool rejected its unknown ID. Controlled synthetic probes demonstrated that the configured endpoint reads both plaintext and OpenAI-produced encrypted agent tasks and returns the requested verification marker. Encryption alone therefore does not establish an account-owned response reference for this protocol item.

## Goals / Non-Goals

**Goals:** Accept complete inline agent tasks consistently in ownership extraction and direct-source classification, preserving payloads and existing continuity boundaries.

**Non-Goals:** Decrypting content, changing credential selection, accepting arbitrary encrypted state, mapping parent account credentials to custom sources, or changing subscription replay.

## Decisions

- Use one strict shape predicate in `replay_safety.py`, shared with `OwnershipScope.request_keys`. Remove validated agent messages only from the classification copy, as with standalone client notifications. Leave the forwarding body untouched.
- Treat encrypted content only inside this exact protocol envelope as inline task data. Ordinary reasoning/compaction ciphertext remains owned. Never strip or reinterpret ciphertext to manufacture a plaintext task.
- Keep response publication unchanged; a later bare item reference still needs ownership. A full inline message can be replayed independently of its bookkeeping ID.
- Rely on the upstream to implement the advertised Responses item. An endpoint that does not support it can return its normal protocol error; rejecting all such items locally prevented working endpoints from receiving them.

## Risks / Trade-offs

- Broad shape acceptance could hide upstream state → allowlist envelope and part fields, validate types, and exercise malformed/file/reference mutations through real routes.
- Unrelated state might become portable by association → regression tests mix valid agent content with reasoning, response and call references, including cross-replica ownership failures.
- Existing metadata formats beyond the supported neutral subset remain rejected → do not expand unrelated metadata behavior without evidence.

## Migration Plan

No migration or source configuration changes. Verify route coverage, independent review and strict specs, then roll out through the existing HA surge deployment and repeat a synthetic encrypted delegation through the public route.
