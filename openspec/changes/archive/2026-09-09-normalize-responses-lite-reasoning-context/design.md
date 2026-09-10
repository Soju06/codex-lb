## Context

codex-lb already derives Responses Lite signaling from a normalized
`additional_tools` input prefix and preserves that signal through HTTP,
compact, direct websocket, HTTP bridge, fallback, prewarm, and trusted
incremental continuity. It does not currently couple that signal to the
upstream body invariant introduced for Lite clients.

Issue #1411 records 273 production rejections from 2026-07-14 through
2026-07-20 (217 on `gpt-5.6-sol`, 47 on `gpt-5.6-terra`, and 9 on
`gpt-5.6-luna`) with the upstream error
`X-OpenAI-Internal-Codex-Responses-Lite requires reasoning.context to be all_turns`.
The observed path was raw HTTP with the bridge disabled, but the same
inconsistent signal/body pair can be produced by every supported Lite
transport.

`ResponsesReasoning` deliberately allows extra fields, so globally narrowing
its schema to one context literal would reject non-Lite or future-compatible
payloads. Existing Lite classification and websocket continuity rules are also
security-sensitive: an inbound header or stale client-metadata marker is not
trusted by itself.

## Goals / Non-Goals

**Goals:**

- Guarantee exact `reasoning.context = "all_turns"` in every final upstream
  payload that codex-lb canonically advertises as Responses Lite.
- Preserve `effort`, `summary`, and unknown reasoning members while repairing
  missing or incompatible context values.
- Cover body-derived, bridge-preserved, and continuity-trusted signaling on
  HTTP, compact, websocket, fallback, and replay serialization paths.
- Keep the normalizer idempotent, apply it before whole-payload byte
  calculations, and preserve the existing input-only compact budget semantics.

**Non-Goals:**

- Broaden Lite detection, trust an inbound Lite header or marker, or establish
  websocket continuity before upstream acceptance.
- Reject otherwise-valid Lite requests because an older client omitted or sent
  a conflicting context value.
- Remove the Lite signal or reinterpret the `additional_tools` contract.
- Constrain or rewrite reasoning context on non-Lite requests.
- Add settings, database state, frontend behavior, or user-facing docs.

## Decisions

### Normalize the body instead of rejecting or disabling Lite

Once the canonical Lite header or websocket marker is emitted, upstream
requires the all-turns reasoning context. The proxy will repair that invariant
rather than return a client error. Rejection would preserve the observed
client-version failure, while stripping the Lite signal would silently break
the `additional_tools` request shape.

The repair creates a reasoning object when it is absent or null. For an existing
object, it shallow-copies the object, sets `context` to the exact string
`"all_turns"`, and preserves every sibling. Missing, null, blank,
differently-cased, other-string, and non-string context values all converge on
the same canonical output.

Alternative considered: make `ResponsesReasoning.context` a required
`Literal["all_turns"]`. Rejected because the constraint is conditional on the
final Lite disposition and non-Lite payloads remain pass-through compatible.

### Finalize a wire dictionary adjacent to canonical signaling

Add one idempotent dictionary-level finalizer beside the existing Responses
Lite body/header/marker helpers. Callers pass the already-decided final Lite
disposition; the helper does not inspect an untrusted inbound header and does
not decide continuity trust.

Apply it at all signal-bearing serialization boundaries:

- Raw Responses transport preparation in `app/core/clients/proxy.py`, before
  the HTTP/websocket split and payload-size calculation, so websocket handshake
  fallback to HTTP keeps the same invariant while swapping marker for header.
- Compact preparation before the final input-budget validation and POST,
  without changing what that input-only budget measures.
- Direct websocket response-create serialization in both the ordinary and
  size-guarded/replay builders.
- HTTP bridge response-create serialization after marker preservation and input
  prefix trimming have produced the final request dictionary.

This placement keeps the validated request model unchanged. A fresh replay that
severs Lite linkage rebuilds from the unmodified model and is not accidentally
normalized unless its final body or trusted marker independently qualifies.

Alternative considered: normalize once during initial Pydantic validation.
Rejected because final transport selection, bridge trimming, and trusted
marker-only continuity are determined later, while some replays deliberately
drop Lite linkage.

### Test disposition and body as one transport contract

Focused helper tests will parameterize absent, null, incorrect-string,
wrong-type, and already-canonical context values, assert sibling preservation,
and prove a non-Lite no-op. Transport and product-path tests will assert the
final serialized body together with its header or marker for raw HTTP, compact,
direct websocket, websocket-to-HTTP fallback, and HTTP
bridge/trusted-continuation paths. Tests that monkeypatch above core egress and
inspect `ResponsesRequest.to_payload()` remain model-preservation tests; they do
not assert a wire-only mutation that has not run yet.

The existing marker-linkage tests remain the authority for classification and
trust. New assertions extend them without replacing or weakening their stale,
wrong-model, and wrong-previous-response negative cases.

## Risks / Trade-offs

- **An untrusted marker triggers normalization** → Key the finalizer only from
  the canonical disposition produced by existing body/trust logic and retain
  stale-marker negative tests.
- **One retry or bridge serializer misses the invariant** → Use one helper and
  exercise every final serialization family, including size-guarded replay.
- **Normalization changes websocket size after measurement** → Run it before
  whole-payload size calculation and trimming; keep compact's existing
  input-only budget validation in its current order before POST.
- **Reasoning extensions are lost** → Shallow-copy the existing mapping and
  parameterize custom sibling fields in tests.
- **A marker-stripped fresh replay retains a mutation** → Mutate only final wire
  dictionaries, never the reusable request model.

## Migration Plan

1. Add focused finalizer tests, including non-Lite and untrusted-marker no-ops.
2. Apply the finalizer to raw Responses and compact transport preparation.
3. Apply it to direct websocket, bridge, fallback, and replay serializers.
4. Extend product-path regressions and run the relevant proxy, architecture,
   lint, type, and strict OpenSpec gates.
5. Open one focused PR with `Fixes #1411` after current-head CI and Codex review.

Rollback is a code-only revert. There is no schema, configuration, or persisted
state migration.

## Open Questions

None. The normalization value, qualifying signal dispositions, and transport
coverage are fixed by the upstream error and existing Lite trust contract.
