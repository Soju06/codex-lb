# Proposed PR split

## PR 1: Canonical Codex WebSocket stale-anchor signal

Scope:

- Rename the sanitized Codex-native classifier to `previous_response_not_found`.
- Preserve raw-envelope and stale-id masking.
- Keep public `/v1/responses` masking on `stream_incomplete`.
- Recognize only the exact parameter-less `Invalid previous_response_id` variant.
- Preserve parameter presence through shared WebSocket normalization.

Primary files/hunks:

- `app/core/errors.py`
- Codex-native WebSocket sanitizer/export hunks in `app/modules/proxy/service.py`
- WebSocket helper/mixin hunks that expose the canonical sanitized classifier
- `tests/unit/test_openai_errors.py`
- Canonical-signal assertions in `tests/integration/test_proxy_websocket_responses.py`

This PR must not include HTTP bridge account migration, circuit bypass, operation
rebind, journal, quarantine, or transport-retry behavior.

## PR 2: HTTP bridge stale-anchor recovery transaction

Base: PR 1.

Scope:

- Explicit-rejection-only full-context replay.
- Account-neutral versus same-owner replay safety.
- Durable operation fencing and inserted-versus-rebound rollback semantics.
- Original-hard-key circuit generation capture and send-adjacent revalidation.
- Central denial of transport-only anchor removal and all verified redispatch.
- Circuit preservation, quarantine cleanup, forwarding, and negative coverage.

Primary files/hunks:

- `app/modules/proxy/_service/http_bridge/**`
- HTTP-specific request-state fields in `app/modules/proxy/_service/support.py`
- Durable operation snapshot/repository/coordinator changes
- `tests/unit/test_proxy_http_bridge.py`
- `tests/unit/test_bridge_ring_lifecycle.py`
- `tests/integration/test_http_responses_bridge.py`

## Overlap handling

`app/core/errors.py`, `app/modules/proxy/service.py`, and WebSocket helpers contain
shared seams. PR 1 owns canonical classification and parameter-preserving error
normalization. PR 2 must consume those APIs without reintroducing signal-shape
changes. Build PR 2 on PR 1 rather than independently cherry-picking overlapping
files.
