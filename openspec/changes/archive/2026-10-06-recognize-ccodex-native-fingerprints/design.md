# Design: Recognize CCodex Native Fingerprints

## Decision

Extend `_is_native_codex_request` with a fingerprint-only allowlist for the exact `ccodex-internal` and `ccodex-handoff-worker` originators. Add only the corresponding slash-terminated User-Agent prefixes. Keep the existing native-originator allowlist used by transport selection unchanged.

## Request flow

Both HTTP and WebSocket upstream header builders call `_is_native_codex_request`. When either reported CCodex identity matches, the builders preserve the inbound User-Agent, originator, and version instead of applying `_normalize_non_native_upstream_fingerprint`. Unlisted CCodex-like identifiers continue through the existing normalization path.

## Verification

Exercise both builders with originator-only and User-Agent-only matches, plus unlisted originators and prefixes. Exercise the HTTP Responses route with an upstream stub to verify the fingerprint reaches the outbound request. Assert that the CCodex fingerprint allowlist alone does not opt requests into WebSocket transport selection.

## Scope

Do not add configuration, routing behavior, or a new upstream error classification. Stable release #2174 and the distinct error-code request from issue #2560 remain separate work.
