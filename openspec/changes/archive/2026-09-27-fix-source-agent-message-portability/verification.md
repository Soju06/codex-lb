# Verification: inline source agent messages

## Completeness and correctness

The single added requirement has four scenarios. `inline_agent_message_is_source_neutral` validates the exact envelope and content parts. `OwnershipScope.request_keys` and `transcript_is_source_free` share it; only the latter's classification copy omits these messages. Forwarding and upstream output publication are unchanged.

- Expanded-pool task and replica change: the route suite covers one-to-five permitted sources, a second app instance, plaintext/encrypted tasks, JSON/SSE and canonical/trailing-slash URLs. It asserts exact forwarded input and finalized reservations.
- Accompanying owned state: route cases cover same/unknown/conflicting response owners, disabled/replaced/disallowed sources, a different client key, plus retained reasoning/call state without a response anchor.
- Malformed envelopes: unit mutation cases and real routes reject unknown fields, bad values and opaque/file-bearing parts. File inputs preserve the pre-existing subscription file-owner path; unavailable subscription routing releases its reservation.
- Bare local IDs: route cases verify that later `item_reference` use is still denied without an upstream publication. Unit tests verify output publication and subscription replay remain unchanged.

## Validation

- SQLite: 44 new unit cases and 52 new route cases verified across the final run (94 passed) and the corrected two file-reservation assertions (2 passed). The initial failures were test expectations about the existing subscription file route, not application changes.
- Replay/source unit regression selection: 426 passed.
- Independent Codex review: no actionable findings; reviewer independently reported 457 scoped unit tests passed.
- Clean review checkout: repository-wide Ruff check, Ruff formatting and `ty check` passed.
- Strict OpenSpec: change valid and 65 main specifications passed after sync.
- PostgreSQL 18: 170 route regressions verified across the broad run (168 passed; two pre-fix test expectations failed) and the corrected file-route rerun (2 passed). The broad process had loaded the old expectations before their correction. No application test failures remain.

## Live compatibility evidence

A synthetic encrypted task produced by subscription `gpt-6-astra` was read successfully by all five configured `ch/linxaq` source credentials, each returning HTTP 200 and the expected marker. The public Codex route rejected the same envelope before this patch with HTTP 409 / `model_source_owner_unavailable` (synthetic request `81901f95-ecf9-4f44-805e-45990dbcd2cf`). No raw user content, tokens or ciphertext were persisted.

## Coherence and limits

The implementation follows the existing standalone-notification classification pattern without rewriting payloads, introducing settings, mutating credentials or migrating ownership records. No frontend or schema changes. Live post-deployment verification is tracked in the deployment report; it is not claimed by local tests.
