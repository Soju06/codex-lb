# Proposed PR split

The upstream contribution remains split into the two PRs below. This repository
candidate combines the selected coherent fixes into one reviewed rollout so
their shared seams and recovery invariants are verified together. That delivery
boundary applies only to this candidate; it does not imply upstream maintainer
approval or alter the upstream split plan.

## PR 1: Canonical Codex WebSocket stale-anchor signal

Scope:

- Rename the sanitized Codex-native classifier to `previous_response_not_found`.
- Preserve raw-envelope and stale-id masking.
- Keep public `/v1/responses` masking on `stream_incomplete`.
- Recognize only the exact parameter-less `Invalid previous_response_id.` variant.
- Preserve parameter presence and raw values through shared WebSocket
  normalization for internal classification; client-facing serializers omit
  malformed values and trim valid strings.

Primary files/hunks:

- `app/core/errors.py`
- `app/core/openai/models.py`
- `app/modules/proxy/helpers.py`
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
- Account-neutral original-hard-key circuit generation capture, CAS, and
  send-adjacent revalidation; same-owner replay instead uses a unique
  owner-pinned key and does not consume the original generation.
- Central denial of transport-only anchor removal and all verified redispatch.
- Circuit preservation, quarantine cleanup, forwarding, and negative coverage.

Primary files/hunks:

- `app/modules/proxy/_service/http_bridge/**`
- HTTP-specific request-state fields in `app/modules/proxy/_service/support.py`
- Durable operation snapshot/repository/coordinator changes
- `app/db/models.py` and
  `app/db/alembic/versions/20260821_000000_add_retry_circuit_admission_generation.py`
- `tests/unit/test_proxy_http_bridge.py`
- `tests/unit/test_bridge_ring_lifecycle.py`
- `tests/integration/test_http_responses_bridge.py`
- `tests/integration/test_migrations.py`

## Overlap handling

`app/core/errors.py`, `app/modules/proxy/service.py`, and WebSocket helpers contain
shared seams. PR 1 owns canonical classification and presence-aware internal
error normalization; client-facing serializers still canonicalize parameters at
the boundary. PR 2 must consume those APIs without reintroducing signal-shape
changes. Build PR 2 on PR 1 rather than independently cherry-picking overlapping
files.
